"""The invoice list: read from Excel/CSV or pasted text, kept as ``Target`` rows, plus the downloadable templates.

pandas/openpyxl are imported inside the functions, so a worker process that only needs the data classes stays light.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .amounts import parse_amount
from .models import Target

_INVOICE_NAMES = ("invoice no", "invoice number", "invoice_no", "invoice num", "inv no", "inv. no", "invoice", "bill no")
_DATE_NAMES = ("invoice date", "invoice_date", "inv date", "inv. date", "date", "bill date")
_AMOUNT_NAMES = ("invoice amount", "invoice value", "total invoice value", "amount", "invoice_amount", "net amount",
                 "total amount", "grand total", "value", "total")
MAX_ROWS = 200_000


@dataclass
class ListInfo:
    targets: list = field(default_factory=list)       # list[Target], first-seen order, de-duplicated
    rows: int = 0                                     # rows with an invoice number
    duplicates: int = 0                               # rows repeated exactly (same number, date and amount)
    has_dates: bool = False
    has_amounts: bool = False
    columns: list = field(default_factory=list)       # the names of the extra columns (usable in the stamp text)
    invoice_column: str = ""
    warnings: list = field(default_factory=list)


def _norm(name) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def _pick(columns: dict, names) -> Optional[str]:
    for wanted in names:
        if wanted in columns:
            return columns[wanted]
    return None


def clean_invoice(raw) -> str:
    """12345.0 -> "12345"; surrounding spaces and a leading apostrophe (Excel's text marker) are dropped."""
    if raw is None:
        return ""
    if isinstance(raw, float):
        if raw != raw:                         # NaN
            return ""
        if raw.is_integer():
            raw = int(raw)
    text = str(raw).strip().lstrip("'").strip()
    return "" if text.lower() in ("nan", "none", "nat") else text


def _to_date(raw) -> Optional[date]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    try:
        import pandas as pd
        parsed = pd.to_datetime(text, dayfirst=True, errors="coerce")
        return None if pd.isna(parsed) else parsed.date()
    except Exception:
        return None


def targets_from_frame(df) -> ListInfo:
    """Rows of a table -> a list of targets. The invoice number column is found by name (else the first column)."""
    info = ListInfo()
    if df is None or len(df) == 0:
        return info
    # Headers typed in Excel often carry a stray space ("Co Code "), which would otherwise show up inside {placeholders}.
    df = df.rename(columns={c: re.sub(r"\s+", " ", str(c)).strip() for c in df.columns})
    columns = {_norm(c): c for c in df.columns}
    invoice_col = _pick(columns, _INVOICE_NAMES) or df.columns[0]
    date_col = _pick(columns, _DATE_NAMES)
    amount_col = _pick(columns, _AMOUNT_NAMES)
    if date_col == invoice_col:
        date_col = None
    if amount_col in (invoice_col, date_col):
        amount_col = None
    extras = [c for c in df.columns if c not in (invoice_col, date_col, amount_col) and str(c).strip()]
    info.invoice_column = str(invoice_col)
    info.columns = [str(c) for c in extras]

    seen = set()
    for record in df.to_dict("records"):
        invoice = clean_invoice(record.get(invoice_col))
        if not invoice:
            continue
        info.rows += 1
        when = _to_date(record.get(date_col)) if date_col is not None else None
        amount = parse_amount(record.get(amount_col)) if amount_col is not None else None
        key = (invoice, when, amount)
        if key in seen:
            info.duplicates += 1
            continue
        seen.add(key)
        extra = {str(c): ("" if _blank(record.get(c)) else str(record.get(c)).strip()) for c in extras}
        info.targets.append(Target(invoice, when, amount, extra))
        if info.rows > MAX_ROWS:
            info.warnings.append(f"Only the first {MAX_ROWS:,} rows of the list are used.")
            break
    info.has_dates = any(t.date for t in info.targets)
    info.has_amounts = any(t.amount is not None for t in info.targets)
    if date_col is None:
        info.warnings.append("No invoice date column found: only the invoice number can be checked.")
    return info


def _blank(value) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)           # NaN
    except Exception:
        return False


def load_list(path: str) -> ListInfo:
    """Read a .xlsx / .xls / .csv invoice list. Raises ValueError with a plain message when it cannot be read."""
    import pandas as pd
    lower = str(path).lower()
    try:
        if lower.endswith(".csv"):
            df = pd.read_csv(path, dtype=object, keep_default_na=False, na_values=[""], encoding_errors="replace")
        else:
            df = pd.read_excel(path, dtype=object)
    except Exception as exc:
        raise ValueError(f"The invoice list could not be read ({exc.__class__.__name__}). "
                         "Use an Excel (.xlsx) or CSV file, or the template.") from exc
    return targets_from_frame(df)


def parse_pasted(text: str) -> ListInfo:
    """Invoice numbers typed or pasted: one per line, or separated by commas / semicolons / tabs."""
    info = ListInfo(invoice_column="Invoice No")
    seen = set()
    for part in re.split(r"[\n\r,;\t]+", text or ""):
        invoice = clean_invoice(part)
        if not invoice:
            continue
        info.rows += 1
        if invoice in seen:
            info.duplicates += 1
            continue
        seen.add(invoice)
        info.targets.append(Target(invoice))
    return info


# ── what is kept between the list being read and the run ───────────────────────────────────────────────────────────

def to_json(info: ListInfo) -> dict:
    return {
        "rows": info.rows, "duplicates": info.duplicates, "has_dates": info.has_dates,
        "has_amounts": info.has_amounts, "columns": info.columns, "invoice_column": info.invoice_column,
        "warnings": info.warnings,
        "targets": [[t.invoice, t.date.isoformat() if t.date else None, t.amount, t.extra] for t in info.targets],
    }


def from_json(data: dict) -> ListInfo:
    info = ListInfo(
        rows=data.get("rows", 0), duplicates=data.get("duplicates", 0), has_dates=data.get("has_dates", False),
        has_amounts=data.get("has_amounts", False), columns=list(data.get("columns", [])),
        invoice_column=data.get("invoice_column", ""), warnings=list(data.get("warnings", [])),
    )
    info.targets = [
        Target(inv, date.fromisoformat(when) if when else None, amount, extra or {})
        for inv, when, amount, extra in data.get("targets", [])
    ]
    return info


def by_invoice(targets) -> dict:
    """{invoice: [Target, ...]} in list order."""
    out: dict = {}
    for target in targets:
        out.setdefault(target.invoice, []).append(target)
    return out


# ── stamp text with the invoice's own values ───────────────────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def format_date(value: Optional[date]) -> str:
    return value.strftime("%d-%b-%Y") if value else ""


def format_amount(value: Optional[float]) -> str:
    if value is None:
        return ""
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    if head:                                           # Indian grouping: 12,34,567
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        whole = f"{head},{tail}"
    return f"{'-' if value < 0 else ''}{whole}.{frac}"


def stamp_text(template: str, target: Optional[Target], page_no: int = 0, document: str = "",
               today: Optional[date] = None) -> str:
    """Fill {invoice}, {date}, {amount}, {today}, {page}, {document} and any column of the list ({LC No} ...).
    A name that is not known stays as typed, so a typo is visible on the stamp rather than silently empty."""
    values = {
        "invoice": target.invoice if target else "", "date": format_date(target.date) if target else "",
        "amount": format_amount(target.amount) if target else "", "today": format_date(today or date.today()),
        "page": str(page_no) if page_no else "", "document": document,
    }
    extra = {_norm(k): v for k, v in (target.extra if target else {}).items()}

    def fill(match):
        name = match.group(1)
        key = _norm(name)
        if key in values:
            return values[key]
        if key in extra:
            return extra[key]
        return match.group(0)

    return _PLACEHOLDER.sub(fill, template or "")


# ── templates ──────────────────────────────────────────────────────────────────────────────────────────────────────

def create_template(path: str, kind: str = "full") -> None:
    """``full``: Invoice No, Invoice Date, Amount (dates and amounts can be checked and used on the stamp).
    ``filter``: Invoice No only (the pages are only picked out)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    full = kind != "filter"
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    headers = ["Invoice No", "Invoice Date", "Amount"] if full else ["Invoice No"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center")
    samples = [("INV-1001", date(2026, 1, 15), 11800.0), ("INV-1002", date(2026, 1, 20), 23600.5)] if full \
        else [("INV-1001",), ("INV-1002",)]
    for row in samples:
        ws.append(list(row))
    ws.column_dimensions["A"].width = 26
    for r in range(2, 2002):
        ws.cell(row=r, column=1).number_format = "@"          # leading zeros survive
    if full:
        ws.column_dimensions["B"].width = 16
        ws.column_dimensions["C"].width = 16
        for r in range(2, 2002):
            ws.cell(row=r, column=2).number_format = "dd-mmm-yyyy"
            ws.cell(row=r, column=3).number_format = "#,##0.00"

    info = wb.create_sheet("How to fill")
    if full:
        lines = [
            "RDC LC Stamper - invoice list", "",
            "Replace the sample rows on the 'Invoices' sheet. Keep the header row. One invoice per row.", "",
            "Invoice No    The number exactly as printed on the invoice. Required.",
            "Invoice Date  A real date. Optional: leave it empty to check the number only.",
            "Amount        The invoice total. Optional: leave it empty to check the number only.", "",
            "Any extra column you add (for example 'LC No') can be printed on the stamp as {LC No}.",
            "{invoice}, {date}, {amount}, {today} and {page} are always available.",
        ]
    else:
        lines = [
            "RDC LC Stamper - filter-only list", "",
            "One invoice number per row, under the header 'Invoice No'.", "",
            "Only the pages that carry these invoice numbers are kept. The report shows, for every",
            "number, whether it was found and on which page.",
        ]
    for line in lines:
        info.append([line])
    info.column_dimensions["A"].width = 100
    info["A1"].font = Font(bold=True, size=13)
    wb.save(path)
