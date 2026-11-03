"""The RDC Sales Register Report: what is reported, fetched from the ERP and
written to one Excel workbook with every value exactly as the ERP returned it.

* The reporting period (Asia/Kolkata): the current month through yesterday; but
  on the first ``PREVIOUS_MONTH_THROUGH_DAY`` days of a month (while the month
  that just ended is still being closed) the whole previous month as well. Each
  month is a file of its own, never two months in one file.
* The rows are streamed from the ERP straight into the workbook, so a report of
  several hundred thousand rows never sits in memory. No value is converted,
  rounded, re-typed or re-formatted: text stays text, numbers stay numbers and
  dates stay dates. The columns, their headings and their order are those of the
  ERP report's RDC Sales Invoice Details template (see ``TEMPLATE_COLUMNS``).
* ``build_workbook`` is a module-level function so it can run in the CPU process
  pool (see app.jobs.run_cpu_phase): the ERP fetch and the writing then never
  slow the web server down.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from operator import itemgetter
from pathlib import Path

from app.jobs import JobCancelled, JobUserError
from app.services.sales_register.sql import SALES_REGISTER_SQL
from app.services.unapplied_receipts.processor import (
    OracleConfig, _connect_oracle, _init_oracle_client, _start_cancel_watcher,
)

# The organization the query is run for. Fixed, as in the report it replaces.
P_ORG_ID = 102

SHEET_NAME = "RDC Sales Register Report"
_MAX_SHEET_ROWS = 1_048_576          # Excel's limit, the header row included
_FETCH_ROWS = 5000
_WIDTH_SAMPLE_ROWS = 500
_DATE_FORMAT = "yyyy-mm-dd hh:mm:ss"
# Characters no spreadsheet file can hold (lone surrogates and the two non-characters).
_UNWRITABLE = re.compile("[\ud800-\udfff￾￿]")


# the previous month stays in the report through this day of the new month (inclusive)
PREVIOUS_MONTH_THROUGH_DAY = 8


# The columns of the ERP report, as its RDC Sales Invoice Details template
# (XXRDC_SALES_INVOICE_REP_V1_NEW_EWB) lays them out: (query column, heading), in the
# template's order. The report shows exactly these; the query columns the template does
# not use (INTERFACE_HEADER_ATTRIBUTE1, SALES_ORDER_NUM, ...) are left out, as in the ERP.
# The template has a Cust PAN Number column but the query returns no PAN, so it is empty,
# as it is in the ERP.
TEMPLATE_COLUMNS = [
    ("GSTIN", "RDC GSTIN"),
    ("SOURCE", "Source"),
    ("INVOICE_NUMBER", "Transaction Number"),
    ("COMMENTS", "Comments"),
    ("INVOICE_DATE", "Invoice Date"),
    ("GL_DATE", "GL Date"),
    ("TRANSACTION_TYPE", "Transaction Type"),
    ("TRANSACTION_NAME", "Transaction Name"),
    ("DOCUMENT_NUMBER", "Document Number"),
    ("REFERENCE", "Reference / SALES ORDER"),
    ("IRN_NUMBER", "IRN"),
    ("ACKN_NUMBER", "Acknowledgement Number"),
    ("ACKN_DATE", "Acknowledgement Date"),
    ("EWBILL", "E-Way BillNumber"),
    ("CUSTOMER_ACCOUNT_NUMBER", "Customer Account Number"),
    ("CUSTOMER_NAME", "Customer Name"),
    ("CUSTOMER_CLASS_CODE", "Customer Classification"),
    ("CUSTOMER_SHIP_TO", "Customer Ship To Add"),
    ("CUSTOMER_BILL_TO", "Customer Bill To Add"),
    ("CUST_GST_NUM", "Cust GST Number"),
    ("CUST_PAN_NUM", "Cust PAN Number"),
    ("ITEM_NAME", "Item Name"),
    ("ITEM_DESCRIPTION", "Item Description"),
    ("CODE", "HSN/SAC Code"),
    ("QUANTITY_INVOICED", "Quantity Invoiced"),
    ("UNIT_SELLING_PRICE", "Taxable Value (Per Unit)"),
    ("TOTAL_INVOICE_AMOUNT_WOT", "Taxable Value (Total QTY)"),
    ("INTERFACE_HEADER_ATTRIBUTE2", "Order Type"),
    ("INTERFACE_HEADER_ATTRIBUTE3", "Delivery"),
    ("PO_NUMBER", "Po Number"),
    ("ORAGANIZATION_NAME", "Organization Name"),
    ("TOTAL_LINE_AMOUNT", "Total Invoice Value (Incl TCS)"),
    ("TAX_INVOICE_NUM", "Tax Invoice Number"),
    ("CGST", "CGST Tax"),
    ("SGST", "SGST Tax"),
    ("IGST", "IGST Tax"),
    ("TCS", "TCS TAX"),
    ("CGST_CENT", "CGST Tax Rate"),
    ("CGST_AMT", "CGST Amount"),
    ("SGST_CENT", "SGST Tax Rate"),
    ("SGST_AMT", "SGST Amount"),
    ("IGST_CENT", "IGST Tax Rate"),
    ("IGST_AMT", "IGST Amount"),
    ("TCS_CENT", "TCS Tax Rate"),
    ("TCS_AMT", "TCS Amount"),
    ("ACCOUNT_CLASS", "Account Class"),
    ("GL_CODE_COMBINATION", "GL Code Combination"),
    ("GL_POSTED_DATE", "GL Posted Date"),
    ("LOCATION", "Location"),
    ("SALES_PER", "Sales Person"),
]


def _column_picker(names: list[str]) -> tuple[list[str], "callable"]:
    """The template's headings and a function turning one fetched row into the
    template's cells. A template column the query does not return is left empty."""
    position = {str(name).upper(): i for i, name in enumerate(names)}
    blank = len(names)
    picks = [position.get(column, blank) for column, _ in TEMPLATE_COLUMNS]
    headings = [heading for _, heading in TEMPLATE_COLUMNS]
    get = itemgetter(*picks)
    if len(picks) == 1:                      # itemgetter then returns the bare value, not a tuple
        return headings, lambda row: (get((*row, None)),)
    return headings, lambda row: get((*row, None))


def get_report_periods(run_date: date) -> list[tuple[str, date, date]]:
    """(label, inclusive_start, inclusive_end) of what this run reports, one entry
    per calendar month and so one file per month: just the month so far on a normal
    day, but through the first ``PREVIOUS_MONTH_THROUGH_DAY`` days of a month the
    whole previous month (its own file) and then the month so far (its own file;
    there is none yet on the 1st)."""
    yesterday = run_date - timedelta(days=1)
    month_start = run_date.replace(day=1)
    if run_date.day <= PREVIOUS_MONTH_THROUGH_DAY:
        previous_month_end = month_start - timedelta(days=1)
        periods = [("Previous Month", previous_month_end.replace(day=1), previous_month_end)]
        if yesterday >= month_start:
            periods.append(("Current Month MTD", month_start, yesterday))
        return periods
    return [("Current Month MTD", month_start, yesterday)]


def report_file_stem(start: date, end: date) -> str:
    return f"RDC_Sales_Register_Report_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}"


def _text_lobs(cursor, metadata):
    """Read a CLOB / BLOB column as plain text / bytes while fetching."""
    import oracledb
    if metadata.type_code in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
    if metadata.type_code == oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
    return None


def _cell(value):
    """The value as the workbook holds it: unchanged, except bytes (a LOB)
    read as text and the few characters a spreadsheet file cannot hold dropped."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str) and _UNWRITABLE.search(value):
        value = _UNWRITABLE.sub("", value)
    return value


class _Writer:
    """One workbook, on as many sheets as the rows need (Excel holds 1,048,575
    rows a sheet), written row by row."""

    def __init__(self, path: str, columns: list[str]):
        import xlsxwriter
        self.columns = columns
        self.workbook = xlsxwriter.Workbook(path, {
            "constant_memory": True,
            "default_date_format": _DATE_FORMAT,
            # a value is never "interpreted": no text becomes a formula, number or link
            "strings_to_formulas": False,
            "strings_to_numbers": False,
            "strings_to_urls": False,
            "nan_inf_to_errors": True,
        })
        self.header = self.workbook.add_format({"bold": True})
        self.sheets = 0
        self.sheet = None
        self.row = 0
        self.rows = 0
        self.widths = [len(c) for c in columns]
        self.sampled = 0
        self._new_sheet()

    def _new_sheet(self):
        self.sheets += 1
        name = SHEET_NAME if self.sheets == 1 else f"{SHEET_NAME} ({self.sheets})"
        self.sheet = self.workbook.add_worksheet(name[:31])
        self.sheet.write_row(0, 0, self.columns, self.header)
        self.sheet.freeze_panes(1, 0)
        self.row = 1

    def add(self, values) -> None:
        if self.row >= _MAX_SHEET_ROWS:
            self._close_sheet()
            self._new_sheet()
        cells = [_cell(v) for v in values]
        self.sheet.write_row(self.row, 0, cells)
        self.row += 1
        self.rows += 1
        if self.sampled < _WIDTH_SAMPLE_ROWS:
            self.sampled += 1
            for i, v in enumerate(cells):
                if v is not None:
                    self.widths[i] = max(self.widths[i], len(str(v)))

    def _close_sheet(self):
        for i, width in enumerate(self.widths):
            self.sheet.set_column(i, i, min(max(width + 2, 12), 42))
        self.sheet.autofilter(0, 0, max(self.row - 1, 0), len(self.columns) - 1)

    def close(self) -> None:
        self._close_sheet()
        self.workbook.close()


def build_workbook(oracle_cfg: OracleConfig, start: date, end: date, path: str,
                   cancel_event=None, progress_cb=None) -> dict:
    """Run the query for ``start``..``end`` (inclusive) and write every row to
    ``path``. Returns {"rows", "columns", "sheets"}. An empty period still
    gives a workbook holding just the column headings."""
    import math

    def progress(rows: int) -> None:
        if progress_cb:
            fraction = 0.1 + 0.8 * (1 - math.exp(-rows / 150_000))
            progress_cb(fraction, f"Fetched {rows:,} rows from the ERP...")

    _init_oracle_client(oracle_cfg.instant_client_dir)
    conn = _connect_oracle(oracle_cfg)
    conn.outputtypehandler = _text_lobs
    stop_watcher, watcher = _start_cancel_watcher(conn, cancel_event)
    writer = None
    try:
        cur = conn.cursor()
        cur.arraysize = _FETCH_ROWS
        cur.prefetchrows = _FETCH_ROWS
        try:
            if progress_cb:
                progress_cb(0.05, "Running the Sales Register query in the ERP...")
            cur.execute(
                SALES_REGISTER_SQL,
                p_from_date=datetime.combine(start, time.min),
                p_to_date=datetime.combine(end, time.min),
                p_trkinv=None, p_location=None, p_cust_name=None, p_cust_number=None,
                p_org_id=P_ORG_ID,
            )
            columns, pick = _column_picker([d[0] for d in cur.description])
            writer = _Writer(path, columns)
            while True:
                batch = cur.fetchmany(_FETCH_ROWS)
                if not batch:
                    break
                for row in batch:
                    writer.add(pick(row))
                progress(writer.rows)
        except JobCancelled:
            raise
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("ERP fetch cancelled") from exc
            raise JobUserError(f"The ERP sales register query failed: {exc}") from exc
        finally:
            cur.close()
    finally:
        stop_watcher.set()
        if watcher is not None:
            watcher.join(timeout=1)
        conn.close()
    try:
        writer.close()
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    return {"rows": writer.rows, "columns": len(columns), "sheets": writer.sheets}
