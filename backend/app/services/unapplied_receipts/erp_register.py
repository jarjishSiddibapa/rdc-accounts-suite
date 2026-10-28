"""The Unapplied Receipts Register, fetched live from the Oracle ERP.

This is the "Unapplied and Unidentified Receipts Register" query. Its three
report parameters (format option, "no batch" label, currency code) are never
given a value, so all three are bound as NULL: no GL date or batch columns, an
empty batch name for receipts without a batch, and amounts in the functional
currency (acctd_amount_applied_from).

The rows come back under the column names of the register export the report
has always been fed with, so the processor reads them exactly as it read the
uploaded file.
"""

from __future__ import annotations

import pandas as pd

from app import config
from app.jobs import JobCancelled, JobUserError
from app.services.unapplied_receipts.processor import (
    OracleConfig, _connect_oracle, _init_oracle_client, _start_cancel_watcher,
)

UNIDENTIFIED_NAME = "***** Unidentified"

# The register query as supplied; the SQL*Plus placeholder comments of the
# original report (&lp_in_..., &ACCT_FLEX_BAL_SEG, &P_SORT) are left out, as
# nothing fills them in. {hint} is an optional optimizer hint (see below).
REGISTER_SQL = """
SELECT {hint}
    SUBSTRB(party.party_name, 1, 50) customer_name,
    cust.account_number customer_number,
    MAX(
        DECODE(
            UPPER(:p_in_format_option),
            'DETAIL', app.gl_date,
            NULL
        )
    ) gl_date,
    NVL(abs.name, :nls_NO_BATCH) batch_source_name,
    NVL(bat.name, :nls_NO_BATCH) batch_name,
    rm.name receipt_method,
    rcpt.receipt_number receipt_number,
    rcpt.receipt_date receipt_date,

    SUM(
        DECODE(
            app.status,
            'ACC',
                DECODE(
                    UPPER(:p_in_curr_code),
                    NULL, app.acctd_amount_applied_from,
                    app.amount_applied
                ),
            'OTHER ACC',
                DECODE(
                    app.applied_payment_schedule_id,
                    -7,
                    DECODE(
                        UPPER(:p_in_curr_code),
                        NULL, app.acctd_amount_applied_from,
                        app.amount_applied
                    ),
                    0
                ),
            0
        )
    ) on_account_amt,

    SUM(
        DECODE(
            app.status,
            'UNAPP',
                DECODE(
                    UPPER(:p_in_curr_code),
                    NULL, app.acctd_amount_applied_from,
                    app.amount_applied
                ),
            'UNID',
                DECODE(
                    UPPER(:p_in_curr_code),
                    NULL, app.acctd_amount_applied_from,
                    app.amount_applied
                ),
            0
        )
    ) unapplied_amt,

    SUM(
        DECODE(
            app.status,
            'OTHER ACC',
                DECODE(
                    app.applied_payment_schedule_id,
                    -4,
                    DECODE(
                        UPPER(:p_in_curr_code),
                        NULL, app.acctd_amount_applied_from,
                        app.amount_applied
                    ),
                    0
                ),
            0
        )
    ) claim_amt,

    NVL(cust.cust_account_id, 0) customer_id,
    DECODE(cust.cust_account_id, NULL, '*', NULL) unid_flag

FROM
    apps.ar_batch_sources_all abs,
    apps.ar_batches_all bat,
    apps.hz_cust_accounts cust,
    apps.hz_parties party,
    apps.ar_receipt_methods rm,
    apps.gl_code_combinations gc,
    apps.ar_receivable_applications_all app,
    apps.ar_cash_receipt_history_all crh,
    apps.ar_cash_receipts_all rcpt,
    apps.ar_payment_schedules_all ps

WHERE
    app.status IN ('ACC', 'UNAPP', 'UNID', 'OTHER ACC')

    AND NVL(app.confirmed_flag, 'Y') = 'Y'

    AND ps.cash_receipt_id = app.cash_receipt_id
    AND ps.class = 'PMT'

    AND rcpt.cash_receipt_id = app.cash_receipt_id
    AND NVL(rcpt.confirmed_flag, 'Y') = 'Y'

    AND crh.cash_receipt_id = rcpt.cash_receipt_id
    AND crh.first_posted_record_flag = 'Y'

    AND cust.cust_account_id(+) = rcpt.pay_from_customer
    AND cust.party_id = party.party_id(+)

    AND rcpt.receipt_method_id = rm.receipt_method_id

    AND bat.batch_id(+) = crh.batch_id

    AND abs.batch_source_id(+) = bat.batch_source_id

    AND gc.code_combination_id = app.code_combination_id

GROUP BY
    party.party_name,
    cust.account_number,
    NVL(abs.name, :nls_NO_BATCH),
    NVL(bat.name, :nls_NO_BATCH),
    rm.name,
    rcpt.receipt_number,
    rcpt.receipt_date,
    NVL(cust.cust_account_id, 0),
    DECODE(cust.cust_account_id, NULL, '*', NULL)

HAVING
       SUM(DECODE(app.status, 'ACC', app.acctd_amount_applied_from, 0)) != 0
    OR SUM(DECODE(app.status, 'UNAPP', app.acctd_amount_applied_from, 'UNID', app.acctd_amount_applied_from, 0)) != 0
    OR SUM(DECODE(app.status, 'OTHER ACC', app.acctd_amount_applied_from, 0)) != 0

ORDER BY
    party.party_name,
    cust.account_number,
    rcpt.receipt_number,
    MAX(DECODE(UPPER(:p_in_format_option), 'SUMMARY', NULL, app.gl_date)),
    NVL(abs.name, :nls_NO_BATCH),
    NVL(bat.name, :nls_NO_BATCH),
    rm.name,
    rcpt.receipt_date,
    NVL(cust.cust_account_id, 0),
    DECODE(cust.cust_account_id, NULL, '*', NULL)
"""

# ERP column -> the register export's column name.
_COLUMNS = {
    "CUSTOMER_NAME": "Customer Name",
    "CUSTOMER_NUMBER": "Customer Number",
    "RECEIPT_METHOD": "Payment Method",
    "RECEIPT_NUMBER": "Payment Number",
    "RECEIPT_DATE": "Payment Date",
    "ON_ACCOUNT_AMT": "On Account/ Prepayment Amount",
    "UNAPPLIED_AMT": "Unapplied Amount",
}


def register_sql() -> str:
    """The query. The database is asked to run it with ORACLE_REGISTER_PARALLEL_DEGREE
    parallel workers (about 21 s -> 5 s on the full history, same rows); an
    optimizer hint, so a database without parallel query simply ignores it.
    0 leaves it out."""
    degree = config.ORACLE_REGISTER_PARALLEL_DEGREE
    return REGISTER_SQL.format(hint=f"/*+ PARALLEL({degree}) */" if degree > 1 else "")


def register_frame(columns: list[str], rows: list) -> pd.DataFrame:
    """The ERP rows as the register export the processor reads: its column
    names, "DD-MON-YY" payment dates, a blank Customer Number and the
    "***** Unidentified" name for a receipt with no customer."""
    raw = pd.DataFrame.from_records(rows, columns=[c.upper() for c in columns])
    df = raw[[c for c in _COLUMNS if c in raw.columns]].rename(columns=_COLUMNS)

    unidentified = raw["UNID_FLAG"].eq("*") | raw["CUSTOMER_NAME"].isna()
    for col in ("Customer Name", "Customer Number", "Payment Method", "Payment Number"):
        df[col] = df[col].where(df[col].notna(), "").astype(str).str.strip()
    df.loc[unidentified, "Customer Name"] = UNIDENTIFIED_NAME
    df.loc[unidentified, "Customer Number"] = ""

    dates = pd.to_datetime(df["Payment Date"], errors="coerce")
    df["Payment Date"] = dates.dt.strftime("%d-%b-%y").str.upper().where(dates.notna(), "")
    for col in ("On Account/ Prepayment Amount", "Unapplied Amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
    return df.reset_index(drop=True)


def fetch_register_from_erp(oracle_cfg: OracleConfig, log_q=None, cancel_event=None) -> pd.DataFrame:
    """Run the register query against the ERP; the register as a frame."""
    _init_oracle_client(oracle_cfg.instant_client_dir)
    if log_q:
        log_q.put(("info", "Fetching the Unapplied Receipts Register from the ERP ..."))
    conn = _connect_oracle(oracle_cfg)
    stop_watcher, watcher = _start_cancel_watcher(conn, cancel_event)
    try:
        cur = conn.cursor()
        cur.arraysize = 5000
        try:
            cur.execute(register_sql(), p_in_format_option=None, nls_no_batch=None, p_in_curr_code=None)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("ERP fetch cancelled") from exc
            raise JobUserError(f"The ERP register query failed: {exc}") from exc
        finally:
            cur.close()
    finally:
        stop_watcher.set()
        if watcher is not None:
            watcher.join(timeout=1)
        conn.close()

    if not rows:
        raise JobUserError("The ERP returned no unapplied or on-account receipts, so there is nothing to report on.")
    df = register_frame(columns, rows)
    if log_q:
        log_q.put(("ok", f"Fetched {len(df):,} receipts of the register from the ERP"))
    return df
