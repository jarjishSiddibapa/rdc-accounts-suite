"""The RDC Sales Register Report: what is reported, fetched from the ERP and
written to one Excel workbook with every value exactly as the ERP returned it.

* The reporting period (Asia/Kolkata): on a normal day the first day of the
  current month through yesterday; on the first day of a month the previous
  month, first through last day (the current month has no completed day yet).
* The rows are streamed from the ERP straight into the workbook, so a report of
  several hundred thousand rows never sits in memory. No value is converted,
  rounded, re-typed or re-formatted: text stays text, numbers stay numbers and
  dates stay dates. The columns are the ERP's own, in the query's order.
* ``build_workbook`` is a module-level function so it can run in the CPU process
  pool (see app.jobs.run_cpu_phase): the ERP fetch and the writing then never
  slow the web server down.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
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


def get_report_periods(run_date: date) -> list[tuple[str, date, date]]:
    """(label, inclusive_start, inclusive_end) of what this run reports."""
    yesterday = run_date - timedelta(days=1)
    month_start = run_date.replace(day=1)
    periods: list[tuple[str, date, date]] = []
    if run_date.day == 1:
        # a new month has started: the previous calendar month, in full
        periods.append(("Previous Month", yesterday.replace(day=1), yesterday))
    if yesterday >= month_start:
        periods.append(("Current Month MTD", month_start, yesterday))
    return periods


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
            columns = [d[0] for d in cur.description]
            writer = _Writer(path, columns)
            while True:
                batch = cur.fetchmany(_FETCH_ROWS)
                if not batch:
                    break
                for row in batch:
                    writer.add(row)
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
