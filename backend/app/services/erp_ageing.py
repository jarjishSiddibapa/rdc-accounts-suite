"""The open receivables ageing, fetched live from the Oracle ERP.

Used by the Untagged Invoices Report and the GST Invoice Number Adder, which
used to take an uploaded Aging export. The query returns the data under the
column names that export used, so the reports read it the same way.
"""

from __future__ import annotations

import datetime as _dt
import threading
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

import numpy as np
import pandas as pd

from app import config
from app.jobs import JobCancelled, JobUserError
from app.services.unapplied_receipts.processor import (
    OracleConfig,
    _connect_oracle,
    _init_oracle_client,
    _start_cancel_watcher,
)

__all__ = ["ERP_AGEING_SQL", "OracleConfig", "oracle_config", "fetch_ageing_from_erp", "erp_frame", "EitherEvent", "TYPE_COL"]


def oracle_config() -> OracleConfig:
    """The ERP connection, from the ORACLE_* settings in backend/.env."""
    return OracleConfig(
        host=config.ORACLE_HOST,
        port=config.ORACLE_PORT,
        service_name=config.ORACLE_SERVICE_NAME,
        user=config.ORACLE_USER,
        password=config.ORACLE_PASSWORD,
        instant_client_dir=config.ORACLE_INSTANT_CLIENT_DIR,
    )

# The open receivables query, as supplied. The only addition is
# arc.customer_name, which the reports have always carried.
ERP_AGEING_SQL = """
SELECT
    arc.customer_name Customer_Name,
    arc.customer_number Customer_Account,

    (
        SELECT DISTINCT
            RTRIM(
                REGEXP_REPLACE(
                    (
                        LISTAGG(DISTINCT ffv.description, ', ')
                        WITHIN GROUP (ORDER BY ffv.description) OVER ()
                    ),
                    '([^-]*)(-\\1)+($|-)',
                    '\\1\\3'
                ),
                '-'
            )
        FROM
            apps.ra_cust_trx_line_gl_dist_all ctlgd,
            apps.ra_customer_trx_lines_all    rctla,
            apps.gl_code_combinations_kfv     gcc,
            apps.fnd_flex_values_vl           ffv
        WHERE
                ctlgd.customer_trx_line_id = rctla.customer_trx_line_id
            AND rctla.customer_trx_id      = apsa.customer_trx_id
            AND rctla.line_type            = 'LINE'
            AND ctlgd.code_combination_id  = gcc.code_combination_id
            AND ffv.flex_value_set_id      = '1009917'
            AND ffv.flex_value             = gcc.segment3
    ) Location,

    apsa.trx_number,

    DECODE(
        apsa.class,
        'INV', 'Invoice',
        'CM',  'Credit Memo',
        'DM',  'Debit Memo',
        'PMT', 'Receipts'
    ) Invoice_Rcpt_Class,

    NVL(
        (
            SELECT
                ab.overall_credit_limit
            FROM
                apps.hz_cust_profile_amts ab
            WHERE
                    ab.cust_account_id = hca.cust_account_id
                AND ROWNUM = 1
        ),
        (
            SELECT
                ab.overall_credit_limit
            FROM
                apps.hz_cust_profile_amts ab
            WHERE
                    ab.cust_account_id = hca.cust_account_id
                AND ab.overall_credit_limit IS NOT NULL
                AND ROWNUM = 1
        )
    ) creditlimit,

    (
        SELECT
            rtla.due_days
        FROM
            apps.ra_terms_lines      rtla,
            apps.ra_terms            rat,
            apps.ra_customer_trx_all rcta
        WHERE
                rtla.term_id         = rat.term_id
            AND rat.term_id          = rcta.term_id
            AND apsa.customer_trx_id = rcta.customer_trx_id
            AND ROWNUM = 1
    ) payment_term,

    apsa.acctd_amount_due_remaining outstanding_amount,

    apsa.trx_date due_date,

    TRUNC(SYSDATE) - apsa.trx_date over_due_days,

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date <= 30
        THEN apsa.acctd_amount_due_remaining
    END "0_to_30_Days",

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date BETWEEN 31 AND 60
        THEN apsa.acctd_amount_due_remaining
    END "31_to_60_Days",

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date BETWEEN 61 AND 90
        THEN apsa.acctd_amount_due_remaining
    END "61_to_90_Days",

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date BETWEEN 91 AND 120
        THEN apsa.acctd_amount_due_remaining
    END "91_to_120_Days",

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date BETWEEN 121 AND 150
        THEN apsa.acctd_amount_due_remaining
    END "121_to_150_Days",

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date BETWEEN 151 AND 180
        THEN apsa.acctd_amount_due_remaining
    END "151_to_180_Days",

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date BETWEEN 181 AND 360
        THEN apsa.acctd_amount_due_remaining
    END "181_to_360_Days",

    CASE
        WHEN TRUNC(SYSDATE) - apsa.trx_date > 360
        THEN apsa.acctd_amount_due_remaining
    END "365_Days_and_above",

    hca.attribute10 Salesperson

FROM
    apps.ar_payment_schedules_all apsa,
    apps.ar_customers             arc,
    apps.hz_cust_accounts         hca

WHERE
        arc.customer_id            = apsa.customer_id
    AND apsa.status                = 'OP'
    AND apsa.amount_due_remaining <> 0
    AND arc.customer_id            = hca.cust_account_id
"""

# ── the same ageing as on a past date ────────────────────────────────────────
#
# What was open on a date is worked out from the receivables ledger (the payment schedule, every application
# and every adjustment, each with its GL date) the way Oracle's own aging works:
#   * an item counts when its GL date is on or before the date and it was not yet closed on it;
#   * an item that is still open today is counted back from today's balance: what was posted after the date
#     (applications, adjustments, write-offs) is undone. So for a date after everything posted, this is exactly
#     the open ageing above;
#   * an item closed since is built forward from its original amount with what was posted up to the date:
#     an invoice / debit memo: original + adjustments - applied to it (and discounts); a credit memo: the same
#     plus the applications made from it; a receipt: its original (negative) amount + what was applied from it
#     (applications and write-offs). (A receipt reversed since has no application to undo, so it is built forward.)
_AS_ON_WITH = """
WITH sched AS (
    SELECT p.payment_schedule_id, p.customer_trx_id, p.customer_id, p.class, p.cash_receipt_id, p.status,
           p.trx_number, p.trx_date, p.amount_due_original, p.amount_due_remaining rem_now,
           p.acctd_amount_due_remaining acctd_now, NVL(p.exchange_rate, 1) fx
    FROM apps.ar_payment_schedules_all p
    WHERE p.class IN ('INV', 'DM', 'CM', 'PMT')
      AND p.gl_date <= :as_on
      AND p.gl_date_closed > :as_on
      {slice}
),
onto AS (
    SELECT a.applied_payment_schedule_id payment_schedule_id,
           SUM(CASE WHEN a.gl_date <= :as_on THEN a.amount_applied + NVL(a.earned_discount_taken, 0)
                                                 + NVL(a.unearned_discount_taken, 0) END) upto,
           SUM(CASE WHEN a.gl_date >  :as_on THEN a.amount_applied + NVL(a.earned_discount_taken, 0)
                                                 + NVL(a.unearned_discount_taken, 0) END) later
    FROM apps.ar_receivable_applications_all a
    WHERE a.status = 'APP'
      AND a.applied_payment_schedule_id IN (SELECT payment_schedule_id FROM sched WHERE class <> 'PMT')
    GROUP BY a.applied_payment_schedule_id
),
issued AS (
    SELECT a.payment_schedule_id,
           SUM(CASE WHEN a.gl_date <= :as_on THEN a.amount_applied END) upto,
           SUM(CASE WHEN a.gl_date >  :as_on THEN a.amount_applied END) later
    FROM apps.ar_receivable_applications_all a
    WHERE a.status = 'APP'
      AND a.payment_schedule_id IN (SELECT payment_schedule_id FROM sched WHERE class = 'CM')
    GROUP BY a.payment_schedule_id
),
rcpt AS (
    SELECT a.cash_receipt_id,
           SUM(CASE WHEN a.gl_date <= :as_on THEN a.amount_applied END) upto,
           SUM(CASE WHEN a.gl_date >  :as_on THEN a.amount_applied END) later
    FROM apps.ar_receivable_applications_all a
    WHERE a.status IN ('APP', 'ACTIVITY')
      AND a.cash_receipt_id IN (SELECT cash_receipt_id FROM sched WHERE class = 'PMT')
    GROUP BY a.cash_receipt_id
),
adj AS (
    SELECT j.payment_schedule_id,
           SUM(CASE WHEN j.gl_date <= :as_on THEN j.amount END) upto,
           SUM(CASE WHEN j.gl_date >  :as_on THEN j.amount END) later
    FROM apps.ar_adjustments_all j
    WHERE j.status = 'A'
      AND j.payment_schedule_id IN (SELECT payment_schedule_id FROM sched WHERE class <> 'PMT')
    GROUP BY j.payment_schedule_id
)
"""

_AS_ON_FROM = """
FROM
    (
        SELECT t.payment_schedule_id, t.customer_trx_id, t.customer_id, t.class, t.trx_number, t.trx_date,
               t.remaining amount_due_remaining,
               CASE WHEN t.status = 'OP' THEN t.acctd_now + ROUND(t.back * t.fx, 2)
                    ELSE ROUND(t.remaining * t.fx, 2) END acctd_amount_due_remaining
        FROM (
            SELECT s.*,
                   CASE WHEN s.status = 'OP' THEN s.rem_now + s.back ELSE s.forward END remaining
            FROM (
                SELECT sched.*,
                       CASE sched.class
                           WHEN 'PMT' THEN -NVL(rcpt.later, 0)
                           WHEN 'CM'  THEN NVL(onto.later, 0) - NVL(adj.later, 0) - NVL(issued.later, 0)
                           ELSE            NVL(onto.later, 0) - NVL(adj.later, 0)
                       END back,
                       CASE sched.class
                           WHEN 'PMT' THEN sched.amount_due_original + NVL(rcpt.upto, 0)
                           WHEN 'CM'  THEN sched.amount_due_original + NVL(adj.upto, 0)
                                           - NVL(onto.upto, 0) + NVL(issued.upto, 0)
                           ELSE            sched.amount_due_original + NVL(adj.upto, 0) - NVL(onto.upto, 0)
                       END forward
                FROM sched
                LEFT JOIN onto   ON onto.payment_schedule_id   = sched.payment_schedule_id
                LEFT JOIN issued ON issued.payment_schedule_id = sched.payment_schedule_id
                LEFT JOIN rcpt   ON rcpt.cash_receipt_id       = sched.cash_receipt_id
                LEFT JOIN adj    ON adj.payment_schedule_id    = sched.payment_schedule_id
            ) s
        ) t
        WHERE t.remaining <> 0
    ) apsa,
    apps.ar_customers     arc,
    apps.hz_cust_accounts hca

WHERE
        arc.customer_id = apsa.customer_id
    AND arc.customer_id = hca.cust_account_id
"""


def ageing_as_on_sql(part: int | None = None, parts: int = 1) -> str:
    """The ageing as on a date (bind ``:as_on``): the select list of ERP_AGEING_SQL with the ledger-based
    balances above. With ``part`` it is one of ``parts`` disjoint slices of the items (by payment schedule),
    each selecting the schedule id to put the rows back in order."""
    select = ERP_AGEING_SQL.split("\nFROM\n", 1)[0].replace("TRUNC(SYSDATE)", ":as_on")
    if part is not None:
        select = select.replace(
            "SELECT\n    arc.customer_name", "SELECT\n    apsa.payment_schedule_id ps_id,\n    arc.customer_name", 1,
        )
    slice_clause = f"AND MOD(p.payment_schedule_id, {parts}) = {part}" if part is not None else ""
    return _AS_ON_WITH.format(slice=slice_clause) + select + _AS_ON_FROM


DAYS_OS_COL = "Days O/S"
TYPE_COL = "Type (Transaction/Receipt)"

_BUCKET_COLUMNS = {
    "0_TO_30_DAYS": "0-30 Days",
    "31_TO_60_DAYS": "31-60 Days",
    "61_TO_90_DAYS": "61-90 Days",
    "91_TO_120_DAYS": "91-120 Days",
    "121_TO_150_DAYS": "121-150 Days",
    "151_TO_180_DAYS": "151-180 Days",
    "181_TO_360_DAYS": "181-360 Days",
    "365_DAYS_AND_ABOVE": "360+ Days",
}

# ERP column -> the name the reports (and the old Aging export) use.
_ERP_COLUMNS = {
    "CUSTOMER_NAME": "Customer Name",
    "CUSTOMER_ACCOUNT": "Customer Account",
    "LOCATION": "Location Name",
    "SALESPERSON": "Sales Person Name",
    "CREDITLIMIT": "Credit Limit",
    "PAYMENT_TERM": "Payment Terms",
    "TRX_NUMBER": "Invoice No/ Receipt No",
    "DUE_DATE": "Invoice/ Receipt Date",
    "INVOICE_RCPT_CLASS": "Invoice Type",
    "OUTSTANDING_AMOUNT": "Accounted Outstanding",
    "OVER_DUE_DAYS": DAYS_OS_COL,
}
_FETCH_SIZE = 5000


def erp_frame(columns: list[str], rows: list, include_buckets: bool = True) -> pd.DataFrame:
    """Turn the raw ERP rows into the export-shaped frame the reports read:
    columns renamed, an explicit Type (Transaction/Receipt), text trimmed.
    The query's own 0-30 / 31-60 ... buckets are kept unless `include_buckets`
    is False (a report that works out its own buckets does not want them)."""
    raw = pd.DataFrame.from_records(rows, columns=[c.upper() for c in columns])
    wanted = dict(_ERP_COLUMNS)
    if include_buckets:
        wanted.update(_BUCKET_COLUMNS)
    df = raw[[c for c in wanted if c in raw.columns]].rename(columns=wanted)

    for col in ("Customer Name", "Customer Account", "Location Name", "Sales Person Name",
                "Invoice No/ Receipt No", "Invoice Type"):
        df[col] = df[col].where(df[col].notna(), "").astype(str).str.strip()
    df["Invoice/ Receipt Date"] = pd.to_datetime(df["Invoice/ Receipt Date"], errors="coerce")
    df["Accounted Outstanding"] = pd.to_numeric(df["Accounted Outstanding"], errors="coerce")
    # due_days as a plain whole number ("30"), nothing when there is none or it is 0
    terms = pd.to_numeric(df["Payment Terms"], errors="coerce")
    df["Payment Terms"] = terms.map(lambda v: "" if pd.isna(v) or v == 0 else str(int(v)))

    # Receipts are the 'PMT' class; invoices, credit memos and debit memos are transactions.
    is_receipt = df["Invoice Type"].str.lower().eq("receipts")
    df.insert(df.columns.get_loc("Invoice/ Receipt Date") + 1, TYPE_COL,
              np.where(is_receipt, "Receipts", "Transactions"))
    return df.reset_index(drop=True)


class EitherEvent:
    """An event that reads as set when the job was cancelled or a sibling fetch
    failed - all _start_cancel_watcher needs of one is is_set()."""

    def __init__(self, *events):
        self._events = [e for e in events if e is not None]

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)


def _partition_sql(part: int, parts: int) -> str:
    """The ageing query restricted to one of `parts` disjoint slices of the open
    items (by payment schedule). Together the slices are exactly the full
    result; each also selects the schedule id, to put the rows back in order."""
    sql = ERP_AGEING_SQL.replace(
        "SELECT\n    arc.customer_name", "SELECT\n    apsa.payment_schedule_id ps_id,\n    arc.customer_name", 1,
    )
    return sql + f"\n    AND MOD(apsa.payment_schedule_id, {parts}) = {part}\n"


def _fetch_rows(oracle_cfg: OracleConfig, sql: str, cancel_event, on_rows=None, binds=None):
    """Run `sql` (with its bind values) on its own connection; (column names, rows)."""
    conn = _connect_oracle(oracle_cfg)
    stop_watcher, watcher = _start_cancel_watcher(conn, cancel_event)
    rows: list = []
    try:
        cur = conn.cursor()
        cur.arraysize = _FETCH_SIZE
        try:
            if binds:
                cur.execute(sql, binds)
            else:
                cur.execute(sql)
            columns = [d[0] for d in cur.description]
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise JobCancelled("ERP fetch cancelled")
                batch = cur.fetchmany(_FETCH_SIZE)
                if not batch:
                    break
                rows.extend(batch)
                if on_rows is not None:
                    on_rows(len(batch))
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("ERP fetch cancelled") from exc
            if isinstance(exc, (JobCancelled, JobUserError)):
                raise
            raise JobUserError(f"The ERP ageing query failed: {exc}") from exc
        finally:
            cur.close()
    finally:
        stop_watcher.set()
        if watcher is not None:
            watcher.join(timeout=1)
        conn.close()
    return columns, rows


def fetch_ageing_from_erp(oracle_cfg: OracleConfig, log_q=None, progress_cb=None,
                          cancel_event=None, include_buckets: bool = True,
                          as_on: _dt.date | None = None) -> pd.DataFrame:
    """Run ERP_AGEING_SQL against Oracle and return the open ageing as the
    export-shaped frame (see erp_frame). Reports progress from 0.05 to 0.25.
    With ``as_on`` (a date) it is the ageing as it stood on that date instead (see ageing_as_on_sql).

    The query's time is the database working out each row's Location and
    payment term, so it is split into ORACLE_FETCH_PARALLELISM slices that run
    at once on their own connections (about 20 s -> 8 s on the full ageing);
    the slices are put back in payment-schedule order, and set to 1 the
    query runs as one, exactly as supplied."""
    _init_oracle_client(oracle_cfg.instant_client_dir)
    if log_q:
        log_q.put(("info", "Fetching the open ageing from the ERP ..." if as_on is None else
                           f"Fetching the ageing as on {as_on.strftime('%d-%b-%Y')} from the ERP ..."))
    parts = max(1, int(config.ORACLE_FETCH_PARALLELISM))
    binds = None if as_on is None else {"as_on": _dt.datetime.combine(as_on, _dt.time())}

    fetched = [0]
    lock = threading.Lock()

    def count(n: int) -> None:
        with lock:
            fetched[0] += n

    def report() -> None:
        if progress_cb:
            progress_cb(0.05 + 0.2 * (1 - np.exp(-fetched[0] / 100_000)),
                        f"Fetched {fetched[0]:,} ageing rows from the ERP...")

    if parts == 1:
        def counted(n: int) -> None:
            count(n)
            report()
        columns, rows = _fetch_rows(
            oracle_cfg, ERP_AGEING_SQL if as_on is None else ageing_as_on_sql(), cancel_event, counted, binds=binds,
        )
    else:
        failed = threading.Event()
        either = EitherEvent(cancel_event, failed)
        first_error: list[BaseException] = []

        def run(part: int):
            try:
                sql = _partition_sql(part, parts) if as_on is None else ageing_as_on_sql(part, parts)
                return _fetch_rows(oracle_cfg, sql, either, count, binds=binds)
            except BaseException as exc:
                with lock:
                    if not first_error:
                        first_error.append(exc)
                failed.set()   # stop the other slices at once
                raise

        with ThreadPoolExecutor(max_workers=parts, thread_name_prefix="erp-ageing") as pool:
            futures = [pool.submit(run, part) for part in range(parts)]
            # progress is reported from this thread only
            while True:
                done, pending = wait(futures, timeout=0.5, return_when=FIRST_EXCEPTION)
                report()
                if not pending or any(f.exception() for f in done):
                    break
            if first_error:
                raise first_error[0]   # the slice that failed first, not the ones it stopped
            results = [f.result() for f in futures]
        columns = results[0][0]
        rows = sorted((row for _, part_rows in results for row in part_rows), key=lambda row: row[0])

    if not rows:
        raise JobUserError("The ERP returned no open receivables, so there is nothing to report on.")
    df = erp_frame(columns, rows, include_buckets=include_buckets)
    if log_q:
        log_q.put(("ok", f"Fetched {len(df):,} open ageing rows from the ERP"))
    if df["Location Name"].eq("").all():
        raise JobUserError(
            "The ERP returned no location for any row, so no location can be worked out. "
            "Please let the administrator know."
        )
    return df
