"""The report workbook. Real Excel dates and numbers (never text), two layouts:

* stamp mode  - one line per page that carried an invoice, with the date and amount checks;
* filter mode - one line per invoice of the list: found or not, and where.
"""

from __future__ import annotations

from .decide import ST_NOT_FOUND, Decision, Row

_DATE_FORMAT = "dd-mmm-yyyy"
_MONEY_FORMAT = "#,##0.00"


def _columns(rows: list, mode: str, has_dates: bool, has_amounts: bool) -> list:
    """[(header, getter, number_format)] for the layout."""
    cols = [("Invoice No", lambda r: r.invoice, None)]
    if mode == "filter":
        cols.append(("Found", lambda r: "No" if r.status == ST_NOT_FOUND else "Yes", None))
    if has_dates:
        cols += [("Excel Date", lambda r: r.target.date if r.target else None, _DATE_FORMAT),
                 ("PDF Date", lambda r: r.pdf_date, _DATE_FORMAT), ("Date Match", lambda r: r.date_match, None)]
    if has_amounts:
        cols += [("Excel Amount", lambda r: r.target.amount if r.target else None, _MONEY_FORMAT),
                 ("PDF Amount", lambda r: r.pdf_amount, _MONEY_FORMAT), ("Amount Match", lambda r: r.amount_match, None)]
    cols += [("Status", lambda r: r.status, None), ("Document", lambda r: r.document or None, None),
             ("Source Page", lambda r: r.page, "0"), ("Output Page", lambda r: r.out_page, "0"),
             ("Read By", lambda r: {"text": "PDF text", "online": "Online OCR", "offline": "Offline OCR"}.get(r.read_by, "")
              or None, None)]
    return cols


def write_report(decision: Decision, path: str, mode: str, has_dates: bool, has_amounts: bool,
                 summary_lines: list) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = list(decision.rows)
    if mode == "filter":                                   # one line per invoice: a duplicate page is not a new line
        rows = [r for r in rows if not r.status.startswith("⚠ Duplicate")]
    cols = _columns(rows, mode, has_dates, has_amounts)

    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.append([header for header, _, _ in cols])
    fill = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
    widths = [len(header) + 4 for header, _, _ in cols]
    for row in rows:
        values = [getter(row) for _, getter, _ in cols]
        ws.append(values)
        for i, value in enumerate(values):
            if value is not None:
                widths[i] = max(widths[i], min(46, len(str(value)) + 2))
    for i, (header, _, number_format) in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
        if number_format:
            for cell in ws[get_column_letter(i)][1:]:
                cell.number_format = number_format
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(len(rows) + 1, 2)}"

    summary = wb.create_sheet("Summary")
    for line in summary_lines:
        summary.append(list(line) if isinstance(line, (list, tuple)) else [line])
    summary.column_dimensions["A"].width = 44
    summary.column_dimensions["B"].width = 18
    summary["A1"].font = Font(bold=True, size=13)
    wb.save(path)
