"""One reader for every spreadsheet-like upload in the suite.

The file's real type is worked out from its *content*, never its extension, so
a ".xls" that is really an .xlsx (or an Oracle HTML report, or a CSV) still
opens. Supported: .xlsx / .xlsm / .xltx (OOXML), .xlsb (binary OOXML), .xls
(BIFF), Oracle/ERP HTML saved as .xls/.htm/.html, and CSV / TSV / TXT.

Everything comes back as a "grid": a DataFrame read with header=None, so each
caller can find its own header row exactly as it always did. Anything that
can't be read raises JobUserError with a message a user can act on (never a
traceback or a generic "could not open").
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.jobs import JobUserError

# What upload dialogs offer - the frontend's EXCEL_ACCEPT mirrors this.
SUPPORTED_EXTENSIONS = (
    ".xlsx", ".xlsm", ".xltx", ".xltm", ".xlsb", ".xls", ".xlt",
    ".csv", ".tsv", ".txt", ".htm", ".html",
)
SUPPORTED_TEXT = ".xlsx, .xlsm, .xlsb, .xls, .csv (or an ERP .htm/.html export)"

_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_SERIAL_RANGE = (20_000, 80_000)  # Excel date serials for roughly 1954-2118


_UPLOAD_PREFIX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_")


def _name(path) -> str:
    """The user's own file name (save_upload prefixes a uuid on disk)."""
    return _UPLOAD_PREFIX.sub("", Path(path).name)


def detect_format(path: str | os.PathLike) -> str:
    """'xlsx' | 'xlsb' | 'xls' | 'html' | 'csv', by looking inside the file."""
    shown = _name(path)
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError as exc:
        raise JobUserError(f"'{shown}' could not be read from the server. Please upload it again.") from exc
    if not head.strip():
        raise JobUserError(f"'{shown}' is empty. Please upload the file with its data in it.")

    if head[:2] == b"PK":
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as exc:
            raise JobUserError(
                f"'{shown}' looks damaged (it is not a complete Excel file). "
                "Please re-download or re-export it and upload it again."
            ) from exc
        if "xl/workbook.bin" in names:
            return "xlsb"
        if "xl/workbook.xml" in names:
            return "xlsx"
        if "content.xml" in names and "mimetype" in names:
            raise JobUserError(
                f"'{shown}' is an OpenDocument (.ods) file. Please open it in Excel and "
                f"save it as .xlsx, then upload that. Supported: {SUPPORTED_TEXT}."
            )
        raise JobUserError(f"'{shown}' is a compressed file but not an Excel workbook. Supported: {SUPPORTED_TEXT}.")
    if head[:8] == _OLE_MAGIC:
        return "xls"
    if head[:4] == b"%PDF":
        raise JobUserError(f"'{shown}' is a PDF. Please upload the spreadsheet itself. Supported: {SUPPORTED_TEXT}.")

    text_head = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:200].lower()
    if text_head.startswith((b"<html", b"<!doctype html", b"<table", b"<head", b"<body", b"<meta")):
        return "html"
    lowered = head.lower()
    if text_head.startswith(b"<?xml") and (b"<html" in lowered or b"<table" in lowered):
        return "html"
    if text_head.startswith(b"<?xml") or text_head.startswith(b"<workbook"):
        raise JobUserError(
            f"'{shown}' is an XML spreadsheet. Please open it in Excel and save it as .xlsx, "
            f"then upload that. Supported: {SUPPORTED_TEXT}."
        )
    if b"\x00" in head:
        raise JobUserError(f"'{shown}' is not a spreadsheet or text file. Supported: {SUPPORTED_TEXT}.")
    return "csv"


# -- readers ------------------------------------------------------------------

def _read_csv_grid(path: str, nrows: int | None) -> pd.DataFrame:
    raw = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        raise JobUserError(f"'{_name(path)}' uses a text encoding that could not be read. Please save it as CSV (UTF-8).")
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = max(",;\t|", key=sample.count)
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if any(cell.strip() for cell in row):
            rows.append([cell.strip() if cell.strip() != "" else None for cell in row])
        if nrows is not None and len(rows) >= nrows:
            break
    if not rows:
        raise JobUserError(f"'{_name(path)}' has no data rows in it.")
    width = max(len(r) for r in rows)
    return pd.DataFrame([r + [None] * (width - len(r)) for r in rows], dtype=object)


def _fix_xlsb_dates(grid: pd.DataFrame) -> pd.DataFrame:
    """pyxlsb hands dates back as bare serial numbers (it can't see cell number
    formats). Turn the numbers in any column whose header text mentions 'date'
    back into dates - the same trick the GST invoice adder relies on."""
    lo, hi = _SERIAL_RANGE
    probe = grid.head(40)
    for col in grid.columns:
        if not probe[col].map(lambda v: isinstance(v, str) and "date" in v.lower()).any():
            continue

        def convert(v):
            if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool) and lo <= v <= hi:
                return _dt.datetime(1899, 12, 30) + _dt.timedelta(days=float(v))
            return v

        grid[col] = grid[col].map(convert).astype(object)
    return grid


def _excel_error(exc: Exception, path: str, kind: str) -> JobUserError:
    shown = _name(path)
    text = f"{type(exc).__name__} {exc}".lower()
    if "encrypt" in text or "password" in text:
        return JobUserError(f"'{shown}' is password-protected. Please remove the password in Excel, save it, and upload it again.")
    if isinstance(exc, zipfile.BadZipFile) or "not a zip" in text or "bad zip" in text:
        return JobUserError(f"'{shown}' looks damaged or is not a real Excel file. Please re-export it and try again.")
    if "no sheet" in text or "worksheet" in text and "not found" in text:
        return JobUserError(f"'{shown}' has no worksheet to read.")
    return JobUserError(
        f"'{shown}' could not be opened as an Excel ({kind}) file. Please check it isn't damaged and "
        f"that it is the right file. Supported: {SUPPORTED_TEXT}."
    )


def sheet_names(path: str) -> list[str]:
    """Worksheet names of an Excel file ([] for HTML/CSV, which have one table)."""
    kind = detect_format(path)
    try:
        if kind in ("xlsx", "xlsb", "xls"):
            engine = {"xlsx": "openpyxl", "xlsb": "pyxlsb", "xls": "xlrd"}[kind]
            with pd.ExcelFile(path, engine=engine) as book:
                return list(book.sheet_names)
    except Exception as exc:  # noqa: BLE001
        raise _excel_error(exc, path, kind) from exc
    return []


def read_grid(path: str | os.PathLike, sheet: str | int | None = None, nrows: int | None = None,
              log_q=None) -> pd.DataFrame:
    """The sheet (or HTML table / CSV) as a header=None DataFrame."""
    path = str(path)
    kind = detect_format(path)
    shown = _name(path)
    try:
        if kind == "csv":
            return _read_csv_grid(path, nrows)
        if kind == "html":
            from app.services.unapplied_receipts.processor import _read_html_streaming

            grid = _read_html_streaming(path, log_q)
            return grid.head(nrows) if nrows else grid
        engine = {"xlsx": "openpyxl", "xlsb": "pyxlsb", "xls": "xlrd"}[kind]
        grid = pd.read_excel(path, sheet_name=0 if sheet is None else sheet, header=None,
                             nrows=nrows, engine=engine, dtype=object)
        if kind == "xlsb":
            grid = _fix_xlsb_dates(grid)
    except JobUserError:
        raise
    except ValueError as exc:
        if sheet is not None and "worksheet" in str(exc).lower():
            try:
                available = ", ".join(f"'{n}'" for n in sheet_names(path))
            except JobUserError:
                available = "none could be read"
            raise JobUserError(f"'{shown}' has no sheet named '{sheet}'. Sheets in the file: {available}.") from exc
        raise _excel_error(exc, path, kind) from exc
    except Exception as exc:  # noqa: BLE001
        raise _excel_error(exc, path, kind) from exc
    if grid.dropna(how="all").empty:
        raise JobUserError(f"'{shown}' is empty - the first sheet has no data in it.")
    return grid


def find_header_row(grid: pd.DataFrame, markers: str | tuple[str, ...], search_range: int = 40) -> int | None:
    """0-indexed row whose cells contain every marker (case-insensitive, exact
    cell text after trimming), or None."""
    wanted = {m.strip().lower() for m in ((markers,) if isinstance(markers, str) else markers)}
    for i in range(min(search_range, len(grid))):
        cells = {str(v).strip().lower() for v in grid.iloc[i].tolist() if v is not None and not pd.isna(v)}
        if wanted <= cells:
            return i
    return None


def promote_header(grid: pd.DataFrame, row: int) -> pd.DataFrame:
    """Use grid row `row` as the column names and return the rows beneath it."""
    names = [str(v).strip() if v is not None and not pd.isna(v) else "" for v in grid.iloc[row].tolist()]
    seen: dict[str, int] = {}
    unique = []
    for name in names:
        if name and name in seen:
            seen[name] += 1
            unique.append(f"{name}.{seen[name]}")
        else:
            seen[name] = 0
            unique.append(name)
    body = grid.iloc[row + 1:].copy().reset_index(drop=True)
    body.columns = unique
    return body


def read_table(path: str | os.PathLike, header_markers: str | tuple[str, ...] | None = None,
               sheet: str | int | None = None, log_q=None) -> pd.DataFrame:
    """A normal DataFrame (header row promoted, numeric columns typed) from any
    supported file. The header is the first row in the top 40 holding every
    marker; with no marker, or none found, it is the first row - so a column
    check by the caller then explains what is wrong."""
    grid = read_grid(path, sheet=sheet, log_q=log_q)
    row = find_header_row(grid, header_markers) if header_markers else None
    frame = promote_header(grid, 0 if row is None else row)
    return frame.dropna(how="all").reset_index(drop=True).infer_objects()


def _convert_xls_with_formats(path: Path, wb) -> None:
    """BIFF .xls -> openpyxl sheets, keeping each cell's number format (Tally
    shows Dr/Cr through formats) and turning date cells into real dates."""
    import xlrd

    book = xlrd.open_workbook(str(path), formatting_info=True)
    for sheet in book.sheets():
        ws = wb.create_sheet(title=sheet.name[:31])
        for r in range(sheet.nrows):
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    continue
                value = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        value = xlrd.xldate.xldate_as_datetime(value, book.datemode)
                    except (ValueError, OverflowError):
                        pass
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    value = bool(value)
                elif cell.ctype == xlrd.XL_CELL_ERROR:
                    value = None
                target = ws.cell(row=r + 1, column=c + 1, value=value)
                fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                if fmt and fmt != "General":
                    target.number_format = fmt


def to_xlsx(path: str | os.PathLike, out_dir: str | os.PathLike, log_q=None) -> Path:
    """For code that only knows openpyxl: the upload itself when it is already
    an .xlsx/.xlsm; otherwise an equivalent .xlsx (every sheet kept, cell
    positions unchanged) written into `out_dir`. .xls keeps its number formats;
    the other formats cannot, which matters only to files whose meaning lives in
    a cell format (Tally's Dr/Cr), so that is logged as a warning."""
    path = Path(path)
    kind = detect_format(path)
    if kind == "xlsx":
        return path
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    converted = False
    if kind == "xls":
        try:
            _convert_xls_with_formats(path, wb)
            converted = True
        except Exception:  # noqa: BLE001 - fall back to values only
            wb = Workbook()
            wb.remove(wb.active)
    if not converted:
        sheets = sheet_names(str(path)) if kind in ("xlsb", "xls") else [None]
        for name in sheets:
            grid = read_grid(path, sheet=name) if name is not None else read_grid(path)
            ws = wb.create_sheet(title=(name or "Sheet1")[:31])
            for row in grid.itertuples(index=False):
                ws.append([None if (v is None or (isinstance(v, float) and np.isnan(v))) else v for v in row])
        if log_q is not None:
            log_q.put(("warn", f"'{_name(path)}' is a {kind.upper()} file, which doesn't carry cell number formats; "
                               "values are read as stored. Use .xlsx or .xls if the report relies on formats such as Dr/Cr."))
    target = Path(out_dir) / f"{path.stem}.converted.xlsx"
    wb.save(target)
    return target


def _as_report_text(v):
    """A grid cell as the ERP's HTML report would have shown it (text)."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return np.nan
    if isinstance(v, (_dt.datetime, pd.Timestamp)):
        return v.strftime("%d-%b-%Y")
    if isinstance(v, _dt.date):
        return v.strftime("%d-%b-%Y")
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    text = str(v).strip()
    return text if text else np.nan


def read_report_tables(path: str | os.PathLike, anchor: str | None = None, anchor_row: int = 0,
                       log_q=None) -> list[pd.DataFrame]:
    """The tables of an ERP report, in pandas.read_html's list-of-tables shape.

    ERP HTML exports go through read_html as always. A report that arrives as
    real Excel or CSV is one grid; when `anchor` (a header cell) is found in
    it, a copy is added in front, cut so that the anchor row sits at row
    `anchor_row` - where the HTML tables put it - and the full grid follows
    for code that reads the metadata block above the header."""
    kind = detect_format(path)
    shown = _name(path)
    if kind == "html":
        try:
            return pd.read_html(str(path), flavor="lxml")
        except ValueError as exc:
            raise JobUserError(
                f"'{shown}' doesn't look like a valid ERP export - no data table could be found in it. "
                "Please re-download this report directly from the ERP and try again."
            ) from exc
    grid = read_grid(path, log_q=log_q).map(_as_report_text)
    tables = [grid]
    if anchor:
        row = find_header_row(grid, anchor, search_range=60)
        if row is not None:
            tables.insert(0, grid.iloc[max(0, row - anchor_row):].reset_index(drop=True))
    return tables


# -- value cleaning -----------------------------------------------------------

_NUM_JUNK = re.compile(r"[,\s ₹]|^rs\.?|inr", re.IGNORECASE)
_ISO_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
_BLANK_TEXT = ("", "nan", "none", "null", "-", "--")


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or (v is pd.NaT) or str(v).strip().lower() in _BLANK_TEXT


def clean_numeric(series: pd.Series) -> tuple[pd.Series, list[str]]:
    """Numbers from a column that may hold real numbers, '1,234.50', '(500)',
    '500-' or blanks. Returns (float series, up to 5 distinct examples of
    non-blank cells that are not numbers)."""
    bad: list[str] = []

    def convert(v):
        if _is_blank(v) or isinstance(v, bool):
            return np.nan
        if isinstance(v, (int, float, np.integer, np.floating)):
            return float(v)
        text = str(v).strip()
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative, text = True, text[1:-1]
        text = _NUM_JUNK.sub("", text)
        if text.endswith("-"):
            negative, text = True, text[:-1]
        try:
            number = float(text)
        except ValueError:
            bad.append(str(v).strip())
            return np.nan
        return -number if negative else number

    converted = series.map(convert).astype(float)
    return converted, list(dict.fromkeys(bad))[:5]


def parse_dates(series: pd.Series) -> tuple[pd.Series, list[str]]:
    """Dates from real datetimes, Excel serial numbers or text (day-first,
    the Indian convention). Returns (datetime series with NaT for the cells
    that aren't dates, up to 5 distinct examples of those cells)."""
    lo, hi = _SERIAL_RANGE
    bad: list[str] = []

    def convert(v):
        if _is_blank(v):
            return pd.NaT
        if isinstance(v, (_dt.datetime, _dt.date, pd.Timestamp)):
            return pd.Timestamp(v)
        if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
            if lo <= v <= hi:
                return pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(v))
            bad.append(str(v))
            return pd.NaT
        text = str(v).strip()
        try:
            # ISO text (2026-08-01) is year-first; anything else is day-first
            return pd.to_datetime(text, dayfirst=not _ISO_DATE.match(text))
        except (ValueError, TypeError, OverflowError):
            bad.append(text)
            return pd.NaT

    parsed = pd.to_datetime(series.map(convert), errors="coerce")
    return parsed, list(dict.fromkeys(bad))[:5]


def require_html_report(path: str | os.PathLike, label: str) -> None:
    """For reports that are read by the structure and colours of the ERP's own
    HTML export (RDC Payables, Trial Balance, Closing Period): any real
    Excel file would silently lose that, so say so instead of misreading it."""
    kind = detect_format(path)
    if kind in ("xlsx", "xlsb", "xls"):
        raise JobUserError(
            f"'{_name(path)}' is an Excel ({kind.upper()}) file. {label} is read from the ERP's own report "
            "export (.xls / .htm / .html), which carries row colours and layout that an Excel copy does not "
            "keep. Please upload the report exactly as downloaded from the ERP - don't open and re-save it."
        )
