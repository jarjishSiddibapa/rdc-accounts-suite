"""The open receivables ageing, fetched live from the Oracle ERP.

Used by the Untagged Invoices Report and the GST Invoice Number Adder, which
used to take an uploaded Aging export. The query returns the data under the
column names that export used, so the reports read it the same way.
"""

from __future__ import annotations

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

__all__ = ["ERP_AGEING_SQL", "OracleConfig", "oracle_config", "fetch_ageing_from_erp", "erp_frame"]


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


def fetch_ageing_from_erp(oracle_cfg: OracleConfig, log_q=None, progress_cb=None,
                          cancel_event=None, include_buckets: bool = True) -> pd.DataFrame:
    """Run ERP_AGEING_SQL against Oracle and return the open ageing as the
    export-shaped frame (see erp_frame). Reports progress from 0.05 to 0.25."""
    _init_oracle_client(oracle_cfg.instant_client_dir)
    if log_q:
        log_q.put(("info", "Fetching the open ageing from the ERP ..."))
    conn = _connect_oracle(oracle_cfg)
    stop_watcher, watcher = _start_cancel_watcher(conn, cancel_event)
    rows: list = []
    try:
        cur = conn.cursor()
        cur.arraysize = _FETCH_SIZE
        try:
            cur.execute(ERP_AGEING_SQL)
            columns = [d[0] for d in cur.description]
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise JobCancelled("ERP fetch cancelled")
                batch = cur.fetchmany(_FETCH_SIZE)
                if not batch:
                    break
                rows.extend(batch)
                if progress_cb:
                    progress_cb(0.05 + 0.2 * (1 - np.exp(-len(rows) / 100_000)),
                                f"Fetched {len(rows):,} ageing rows from the ERP...")
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
