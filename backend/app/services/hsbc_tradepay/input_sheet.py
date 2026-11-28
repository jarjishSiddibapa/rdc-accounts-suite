"""The Excel the payments can be typed into: the blank template, and reading a filled one back.

Only the three entry columns are read (vendor code, invoice number, amount). Columns are found by their headings, never
by position, and a cell Excel may have changed (a date, or a number where the text matters) is left blank and reported,
so that nothing is guessed in a payment file.
"""

from __future__ import annotations

import io
import math
import numbers
import re
from datetime import date, datetime, time

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.jobs import JobUserError
from app.services import tabular_input

SHEET = "Payments"
HEADERS = ("Vendor Code", "Invoice No.", "Amount (net of TDS)")
FILE_NAME = "HSBC_Tradepay_Input_Template.xlsx"
KINDS = ("xlsx", "xlsb", "xls")

_FIELDS = ("vendor_code", "invoice_no", "amount")
_LABELS = {"vendor_code": "vendor code", "invoice_no": "invoice number", "amount": "amount"}
_LIMITS = {"vendor_code": 40, "invoice_no": 80, "amount": 40}          # what the lookup accepts
_HEADINGS = {                                                          # headings compared by their letters only
    "vendor_code": {"vendorcode", "vendor", "vendorno", "vendornumber", "suppliercode", "supplierno", "suppliernumber"},
    "invoice_no": {"invoiceno", "invoicenumber", "invoicenum", "invoice"},
    "amount": {"amountnetoftds", "amount", "amt", "paymentamount", "paymentamountnetoftds", "netamount"},
}
_HEADER_SEARCH = 20


def template(max_rows: int) -> bytes:
    """A blank workbook: the three headings, with the cells below ready to hold exactly what is typed."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(list(HEADERS))
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for letter, width in (("A", 18), ("B", 30), ("C", 24)):
        ws.column_dimensions[letter].width = width
    for row in range(2, max_rows + 2):
        ws.cell(row=row, column=1).number_format = "@"          # text: leading zeros survive
        ws.cell(row=row, column=2).number_format = "@"          # text: "1/2" is not turned into a date
        ws.cell(row=row, column=3).number_format = "#,##0.00"
    ws.freeze_panes = "A2"

    info = wb.create_sheet("How to fill")
    for line in (
        "RDC HSBC Tradepay - payments to put in the bank file", "",
        f"Type one payment per row on the '{SHEET}' sheet, under the headings. Keep the heading row.", "",
        "Vendor Code           The vendor's code in the ERP.",
        "Invoice No.           The invoice number as it is in the ERP.",
        "Amount (net of TDS)   The amount to pay, after TDS. At most two decimals.", "",
        f"One file can hold up to {max_rows} payments. When pasting, use 'Paste values' so the cells stay as text.",
        "Upload the filled file on the RDC HSBC Tradepay CSV Generation page.",
    ):
        info.append([line])
    info.column_dimensions["A"].width = 100
    info["A1"].font = Font(bold=True, size=13)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _columns(grid: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """(heading row, {field: column}) from the first row that names all three."""
    for number in range(min(_HEADER_SEARCH, len(grid))):
        letters = [re.sub(r"[^a-z]", "", value.lower()) if isinstance(value, str) else ""
                   for value in grid.iloc[number].tolist()]
        found = {field: [i for i, text in enumerate(letters) if text in _HEADINGS[field]] for field in _FIELDS}
        if not all(found.values()):
            continue
        for field, columns in found.items():
            if len(columns) > 1:
                raise JobUserError(f"The file has more than one {_LABELS[field]} column. Keep only the one to be paid.")
        return number, {field: columns[0] for field, columns in found.items()}
    raise JobUserError(f"The file needs the headings {', '.join(HEADERS)} in one row. "
                       "Download the template, fill it and upload that.")


def _cell(value, field: str) -> tuple[str, str]:
    """(the cell as text, what is wrong with it)."""
    if value is None or value is pd.NaT or (isinstance(value, float) and math.isnan(value)):
        return "", ""
    label = _LABELS[field]
    if isinstance(value, (datetime, date, time)):
        wanted = "a number" if field == "amount" else "Text"
        return "", f"Excel holds the {label} as a date. Format the cell as {wanted} and type it again."
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        number = float(value)
        if field == "amount":
            # 125000.00000000001 (a formula) is 125000; real extra decimals are kept, and refused by the check
            rounded = round(number, 2)
            text = f"{rounded:.2f}".rstrip("0").rstrip(".") if abs(number - rounded) < 1e-6 else repr(number)
        elif number.is_integer() and abs(number) < 1e15:
            text = str(int(value))
        else:
            return "", (f"Excel holds the {label} as a number that may differ from what was typed. "
                        "Format the cell as Text and type it again.")
    else:
        text = str(value).strip()
    if len(text) > _LIMITS[field]:
        return "", f"The {label} is too long."
    return text, ""


def read(path: str, max_rows: int) -> tuple[list[dict], list[str]]:
    """([{vendor_code, invoice_no, amount}], notes about cells that could not be taken) of a filled workbook."""
    if tabular_input.detect_format(path) not in KINDS:
        raise JobUserError("Upload an Excel file (.xlsx, .xlsm, .xlsb or .xls): the filled template.")
    names = tabular_input.sheet_names(path)
    sheet = next((name for name in names if name.strip().lower() == SHEET.lower()), None)
    grid = tabular_input.read_grid(path, sheet=sheet)
    header, columns = _columns(grid)
    rows, notes = [], []
    for number in range(header + 1, len(grid)):
        values = grid.iloc[number].tolist()
        cells = {field: _cell(values[column], field) for field, column in columns.items()}
        if not any(text or problem for text, problem in cells.values()):
            continue
        rows.append({field: cells[field][0] for field in _FIELDS})
        notes.extend(f"Row {number + 1} of the sheet: {cells[field][1]}" for field in _FIELDS if cells[field][1])
        if len(rows) > max_rows:
            raise JobUserError(f"The file has more than {max_rows} payments. Split it into two files.")
    if not rows:
        raise JobUserError("No payments were found under the headings. Fill the sheet, save it and upload it again.")
    return rows, notes
