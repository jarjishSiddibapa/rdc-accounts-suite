"""PO -> ERP payment document number, via the accounts team's own proven
query (verbatim - same CTE shape, same three fallback status strings).

Oracle connectivity follows this suite's established per-tool convention
(same shape as gst_invoice_adder/unapplied_receipts's own OracleConfig -
duplicated deliberately rather than shared, per those modules' own
docstrings) rather than a new shared abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass

import oracledb

from app.jobs import JobUserError
from app.oracle_runtime import initialize_oracle_client

_ORACLE_CONNECT_ERROR = (
    "Could not connect to the Oracle ERP database - this isn't an application "
    "bug. Check that the ERP server is reachable from this network and that "
    "ORACLE_HOST/ORACLE_SERVICE_NAME/ORACLE_USER/ORACLE_PASSWORD in backend/.env "
    "are correct, then try again."
)

# The query's own fallback strings when no document number exists yet -
# every submitted PO not covered by one of these (and actually returned by
# the query) carries a real document number instead.
STATUS_PO_NOT_BOOKED = "PO NOT BOOKED"
STATUS_PAYMENT_ENTRY_NOT_MADE = "PAYMENT ENTRY PASSED BUT PAYMENT NOT MADE"
STATUS_BOOKED_PAYMENT_NOT_MADE = "BOOKED - PAYMENT NOT MADE"
NO_DOCUMENT_STATUSES = {STATUS_PO_NOT_BOOKED, STATUS_PAYMENT_ENTRY_NOT_MADE, STATUS_BOOKED_PAYMENT_NOT_MADE}


@dataclass(frozen=True)
class OracleConfig:
    host: str
    port: str
    service_name: str
    user: str
    password: str
    instant_client_dir: str

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"


def init_oracle_client(instant_client_dir: str) -> None:
    initialize_oracle_client(oracledb, instant_client_dir)


_QUERY_TEMPLATE = """
WITH po_list AS
(
    SELECT pha.po_header_id,
           pha.segment1 AS po_number,
           pha.vendor_id AS vendor_id
    FROM APPS.po_headers_all pha
    WHERE pha.segment1 IN ({placeholders})
),
po_invoices AS
(
    SELECT DISTINCT p.po_header_id, p.po_number, aia.invoice_id, aia.invoice_num
    FROM po_list p
    JOIN APPS.ap_invoices_all aia ON aia.po_header_id = p.po_header_id
    UNION
    SELECT DISTINCT p.po_header_id, p.po_number, aia.invoice_id, aia.invoice_num
    FROM po_list p
    JOIN APPS.po_distributions_all pda ON pda.po_header_id = p.po_header_id
    JOIN APPS.ap_invoice_distributions_all aida ON aida.po_distribution_id = pda.po_distribution_id
    JOIN APPS.ap_invoices_all aia ON aia.invoice_id = aida.invoice_id
),
payment_details AS
(
    SELECT
        pi.po_header_id, pi.po_number, pi.invoice_id, pi.invoice_num,
        aca.check_number AS document_number,
        aca.void_date, aca.status_lookup_code, aip.accounting_date,
        ROW_NUMBER() OVER (
            PARTITION BY pi.po_header_id
            ORDER BY CASE WHEN aca.void_date IS NULL THEN 1 ELSE 2 END,
                     aip.accounting_date DESC, aip.invoice_payment_id DESC
        ) AS rn
    FROM po_invoices pi
    JOIN APPS.ap_invoice_payments_all aip ON aip.invoice_id = pi.invoice_id
    JOIN APPS.ap_checks_all aca ON aca.check_id = aip.check_id
)
SELECT
    p.po_number,
    CASE
        WHEN NOT EXISTS (SELECT 1 FROM po_invoices pi WHERE pi.po_header_id = p.po_header_id)
        THEN '{status_not_booked}'
        WHEN EXISTS (SELECT 1 FROM payment_details pd WHERE pd.po_header_id = p.po_header_id AND pd.void_date IS NULL)
        THEN (SELECT TO_CHAR(pd.document_number) FROM payment_details pd WHERE pd.po_header_id = p.po_header_id AND pd.void_date IS NULL AND pd.rn = 1)
        WHEN EXISTS (SELECT 1 FROM payment_details pd WHERE pd.po_header_id = p.po_header_id)
        THEN '{status_entry_not_made}'
        ELSE '{status_booked_not_made}'
    END AS status,
    asup.vendor_name AS vendor_name
FROM po_list p
LEFT JOIN APPS.ap_suppliers asup ON asup.vendor_id = p.vendor_id
"""

# A payment (document/check number) settles specific invoices, and one
# document can cover invoices raised against more than one PO - verified
# live (e.g. document 20270001460 paid invoices against both PO 651842 and
# PO 653660). This is deliberately a separate on-demand query keyed by
# document_number rather than folded into _QUERY_TEMPLATE, since it is only
# ever needed when Accounts/Both drills into one already-resolved payment.
_INVOICE_DETAILS_QUERY = """
SELECT
    aia.invoice_num,
    aia.invoice_date,
    aia.invoice_amount,
    aip.amount AS amount_paid,
    asup.vendor_name,
    COALESCE(
        pha.segment1,
        (
            SELECT pha2.segment1
            FROM APPS.po_distributions_all pda2
            JOIN APPS.ap_invoice_distributions_all aida2 ON aida2.po_distribution_id = pda2.po_distribution_id
            JOIN APPS.po_headers_all pha2 ON pha2.po_header_id = pda2.po_header_id
            WHERE aida2.invoice_id = aia.invoice_id AND ROWNUM = 1
        )
    ) AS po_number
FROM APPS.ap_checks_all aca
JOIN APPS.ap_invoice_payments_all aip ON aip.check_id = aca.check_id
JOIN APPS.ap_invoices_all aia ON aia.invoice_id = aip.invoice_id
LEFT JOIN APPS.ap_suppliers asup ON asup.vendor_id = aia.vendor_id
LEFT JOIN APPS.po_headers_all pha ON pha.po_header_id = aia.po_header_id
WHERE aca.check_number = :document_number
ORDER BY aia.invoice_num
"""


def _build_query(po_numbers: list[str]) -> tuple[str, dict[str, str]]:
    binds = {f"p{i}": po for i, po in enumerate(po_numbers)}
    placeholders = ", ".join(f":{key}" for key in binds)
    query = _QUERY_TEMPLATE.format(
        placeholders=placeholders,
        status_not_booked=STATUS_PO_NOT_BOOKED,
        status_entry_not_made=STATUS_PAYMENT_ENTRY_NOT_MADE,
        status_booked_not_made=STATUS_BOOKED_PAYMENT_NOT_MADE,
    )
    return query, binds


def fetch_document_numbers(oracle_cfg: OracleConfig, po_numbers: list[str]) -> dict[str, tuple[str, str | None]]:
    """Return {po_number: (raw_status_or_document_number, vendor_name)} for
    every PO the query actually returned a row for. A PO absent from the
    result (no row at all) doesn't exist in po_headers_all - the caller must
    treat that distinctly from STATUS_PO_NOT_BOOKED, which means the header
    exists but has no invoice yet. vendor_name comes from the PO header's own
    vendor, so it is present whenever the PO exists, regardless of payment
    status."""
    if not po_numbers:
        return {}
    query, binds = _build_query(po_numbers)
    try:
        conn = oracledb.connect(user=oracle_cfg.user, password=oracle_cfg.password, dsn=oracle_cfg.dsn)
    except Exception as exc:
        raise JobUserError(_ORACLE_CONNECT_ERROR) from exc
    try:
        cursor = conn.cursor()
        cursor.execute(query, binds)
        return {
            str(po): (str(status), str(vendor_name) if vendor_name is not None else None)
            for po, status, vendor_name in cursor.fetchall()
        }
    finally:
        conn.close()


def fetch_invoice_details(oracle_cfg: OracleConfig, document_number: str) -> list[dict]:
    """Return every invoice a payment document actually settled - a single
    document can cover invoices raised against more than one PO (verified
    live), so this is not scoped to any one PO."""
    try:
        conn = oracledb.connect(user=oracle_cfg.user, password=oracle_cfg.password, dsn=oracle_cfg.dsn)
    except Exception as exc:
        raise JobUserError(_ORACLE_CONNECT_ERROR) from exc
    try:
        cursor = conn.cursor()
        cursor.execute(_INVOICE_DETAILS_QUERY, {"document_number": document_number})
        rows = cursor.fetchall()
        return [
            {
                "invoice_number": str(invoice_num),
                "invoice_date": invoice_date.date().isoformat() if invoice_date else None,
                "invoice_amount": float(invoice_amount) if invoice_amount is not None else None,
                "amount_paid": float(amount_paid) if amount_paid is not None else None,
                "vendor_name": str(vendor_name) if vendor_name is not None else None,
                "po_number": str(po_number) if po_number is not None else None,
            }
            for invoice_num, invoice_date, invoice_amount, amount_paid, vendor_name, po_number in rows
        ]
    finally:
        conn.close()
