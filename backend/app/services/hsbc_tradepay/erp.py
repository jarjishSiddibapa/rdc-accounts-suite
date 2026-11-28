"""What the ERP knows about the listed vendors and invoices (read only).

Bank details come from RDC's own vendor bank master, ``apps.xx_vendor_bank_details`` (keyed by the vendor code);
only rows that are approved and not deleted are ever used. The standard Oracle payee bank tables are all but empty
at RDC. Vendor name, type and city come from the supplier master, and each invoice is looked up so the page can
warn about a wrong number, an already paid invoice or an amount larger than what is open.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app import config
from app.oracle_runtime import initialize_oracle_client

logger = logging.getLogger(__name__)

_BATCH = 900                      # Oracle allows 1000 values in one IN list
_CALL_TIMEOUT_MS = 60_000
_FETCH_ROWS = 2000                # per round trip: the default 100 makes a long list several times slower

_SUPPLIERS = """
SELECT s.vendor_id, s.segment1 AS vendor_code, s.vendor_name, s.vendor_type_lookup_code AS vendor_type,
       s.enabled_flag, s.end_date_active, s.hold_all_payments_flag
  FROM apps.ap_suppliers s
 WHERE s.segment1 IN ({values})
"""

_BANK = """
SELECT TRIM(b.fldsupplierid) AS vendor_code, b.fldloccode AS location_code, b.fldbenificiary AS beneficiary,
       b.fldbenifacnum AS account_no, b.fldbenififsc AS ifsc, b.fldbenifbank AS bank, b.fldbenifbranch AS branch,
       b.flddisable AS status, b.flddel AS deleted
  FROM apps.xx_vendor_bank_details b
 WHERE TRIM(b.fldsupplierid) IN ({values})
"""

_SITES = """
SELECT ss.vendor_id, ss.vendor_site_id, ss.city, ss.pay_site_flag, ss.inactive_date, ss.last_update_date
  FROM apps.ap_supplier_sites_all ss
 WHERE ss.vendor_id IN ({values})
"""

_INVOICES = """
SELECT i.invoice_id, i.vendor_id, i.invoice_num, i.invoice_date, i.invoice_amount,
       NVL(i.amount_paid, 0) AS amount_paid, i.payment_status_flag, i.cancelled_date, i.vendor_site_id,
       i.invoice_currency_code AS currency
  FROM apps.ap_invoices_all i
 WHERE i.vendor_id IN ({values}) AND {column} IN ({numbers})
"""


class ErpUnavailable(RuntimeError):
    """The ERP could not be reached; the message is meant for the user."""


@dataclass
class ErpData:
    suppliers: dict[str, dict] = field(default_factory=dict)          # vendor code -> supplier
    bank_rows: dict[str, list[dict]] = field(default_factory=dict)    # vendor code -> every bank master row
    sites: dict[int, list[dict]] = field(default_factory=dict)        # vendor id -> sites
    invoices: dict[tuple[int, str], list[dict]] = field(default_factory=dict)   # (vendor id, NUMBER) -> invoices


def invoice_key(number: str) -> str:
    return " ".join(str(number or "").split()).upper()


def _chunks(values: list, size: int = _BATCH):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _select(cursor, sql: str, **lists) -> list[dict]:
    """Run ``sql``: every ``{name}`` in it becomes one bind variable per value of the list of that name."""
    binds, marks = {}, {}
    for name, values in lists.items():
        keys = [f"{name}{n}" for n in range(len(values))]
        binds.update(zip(keys, values))
        marks[name] = ",".join(f":{key}" for key in keys)
    for name, text in marks.items():
        sql = sql.replace("{" + name + "}", text)
    cursor.execute(sql, binds)
    names = [d[0].lower() for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _connect():
    import oracledb

    if not (config.ORACLE_HOST and config.ORACLE_SERVICE_NAME and config.ORACLE_USER and config.ORACLE_PASSWORD):
        raise ErpUnavailable("The ERP connection is not set up: fill the ORACLE_ settings in backend/.env.")
    initialize_oracle_client(oracledb, config.ORACLE_INSTANT_CLIENT_DIR)
    try:
        connection = oracledb.connect(
            user=config.ORACLE_USER, password=config.ORACLE_PASSWORD,
            dsn=f"{config.ORACLE_HOST}:{config.ORACLE_PORT}/{config.ORACLE_SERVICE_NAME}",
        )
    except Exception as exc:   # a DNS failure surfaces as a plain socket error in thin mode
        logger.exception("HSBC Tradepay: could not connect to the ERP")
        raise ErpUnavailable(
            "Could not connect to the Oracle ERP. Check that the ERP server is reachable, then try again."
        ) from exc
    connection.call_timeout = _CALL_TIMEOUT_MS
    return connection


def fetch(vendor_codes: list[str], invoices: list[tuple[str, str]] = ()) -> ErpData:
    """Suppliers, bank master rows and sites of ``vendor_codes``; with ``invoices`` [(vendor code, number)] also
    those invoices."""
    codes = sorted({code for code in vendor_codes if code})
    data = ErpData()
    if not codes:
        return data
    connection = _connect()
    try:
        cursor = connection.cursor()
        cursor.arraysize = _FETCH_ROWS
        cursor.prefetchrows = _FETCH_ROWS + 1
        for part in _chunks(codes):
            for row in _select(cursor, _SUPPLIERS, values=part):
                data.suppliers[row["vendor_code"]] = row
            for row in _select(cursor, _BANK, values=part):
                data.bank_rows.setdefault(row["vendor_code"], []).append(row)
        ids = sorted(s["vendor_id"] for s in data.suppliers.values())
        for part in _chunks(ids):
            for row in _select(cursor, _SITES, values=part):
                data.sites.setdefault(row["vendor_id"], []).append(row)
        _fetch_invoices(cursor, data, invoices)
    except ErpUnavailable:
        raise
    except Exception as exc:
        logger.exception("HSBC Tradepay: ERP lookup failed")
        raise ErpUnavailable("The ERP lookup failed. Try again; if it keeps failing, contact the administrator.") from exc
    finally:
        connection.close()
    return data


def _fetch_invoices(cursor, data: ErpData, invoices) -> None:
    wanted: dict[tuple[int, str], str] = {}                           # (vendor id, KEY) -> number as typed
    for code, number in invoices:
        supplier = data.suppliers.get(code)
        text = str(number or "").strip()
        if supplier and text:
            wanted[(supplier["vendor_id"], invoice_key(text))] = text

    def collect(rows):
        for row in rows:
            key = (row["vendor_id"], invoice_key(row["invoice_num"]))
            if key in wanted:
                found = data.invoices.setdefault(key, [])
                if all(r["invoice_id"] != row["invoice_id"] for r in found):
                    found.append(row)

    # exactly as typed first (indexed); what is still missing, ignoring capitals and stray spaces
    for column, value_of in (("i.invoice_num", lambda key: wanted[key]),
                             ("UPPER(TRIM(i.invoice_num))", lambda key: key[1])):
        missing = [key for key in wanted if key not in data.invoices]
        for part in _chunks(missing):
            collect(_select(cursor, _INVOICES.replace("{column}", column),
                            values=sorted({key[0] for key in part}),
                            numbers=sorted({value_of(key) for key in part})))
