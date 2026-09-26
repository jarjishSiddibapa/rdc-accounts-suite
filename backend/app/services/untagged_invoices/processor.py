"""Untagged Invoices Report Generator — processing pipeline.

Single input: an Oracle AR "Aging" export (the same raw shape as the
Unapplied Receipts Report's ageing file — see
app.services.unapplied_receipts.processor._read_ageing). Produces one
workbook with 4 sheets, in this fixed order:

  1. Untagged Summary         - Location-wise pivot of the "untagged" rows
                                 (Type = Receipt, Accounted Outstanding < 0)
  2. Untagged Detailed Ageing - the filtered detail rows behind sheet 1
  3. Ageing                   - the full input, with a "Location" column
                                 added right after "Location Name" (mapped
                                 via the centralized Location Name -> Location
                                 mapping) and ageing buckets recomputed
  4. Below 1k                 - Location-wise pivot of rows whose Accounted
                                 Outstanding is between 0 and 1,000

Ageing buckets (Days O/S, 0-30 Days, 31-60 Days, ...) are always recomputed
from (Invoice/ Receipt Date, as_on_date) rather than trusted from the
source file's own bucket columns - those are frozen to whatever "As of
Date" the ERP export itself was run with, so recomputing lets this report
be regenerated as of any date without a fresh ERP export. as_on_date
defaults to today when not given.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, NamedStyle, PatternFill, Border, Side
from openpyxl.cell import WriteOnlyCell
from openpyxl.utils import get_column_letter

from app.jobs import JobUserError
from app.services.report_progress import row_progress_reporter

# ── column name resolution (tolerant of minor header drift - trailing
#    spaces / a stray nbsp are already known to show up in this export,
#    see the "\xa0Sum of Unapplied Amount" / "361-999999 Days " columns) ──

_HEADER_MARKER = "Customer Name"
_LOCATION_NAME_TOKENS = ("location", "name")
_ACCOUNTED_OUTSTANDING_TOKENS = ("accounted", "outstanding")
_INVOICE_DATE_TOKENS = ("invoice", "date")   # excludes "GL Date"
_TYPE_TOKENS = ("transaction", "receipt")    # "Type (Transaction/Receipt)", not "Invoice Type"
_DAYS_OS_COL = "Days O/S"

# Bucket columns, in order - recomputed from (as_on_date - invoice date).days.
BUCKET_COLS = [
    "0-30 Days", "31-60 Days", "61-90 Days", "91-120 Days",
    "121-150 Days", "151-180 Days", "181-360 Days", "361-999999 Days ",
]
_BUCKET_EDGES = (30, 60, 90, 120, 150, 180, 360)  # last bucket = anything above 360

LOCATION_COL = "Location"


def _find_col(columns, *token_sets: tuple[str, ...]) -> str | None:
    """Return the first column whose lowercased name contains every token
    in one of the given token sets (tried in order)."""
    lowered = {c: str(c).lower() for c in columns}
    for tokens in token_sets:
        for col, low in lowered.items():
            if all(t in low for t in tokens):
                return col
    return None


def _bucket_index(days: float) -> int:
    for i, edge in enumerate(_BUCKET_EDGES):
        if days <= edge:
            return i
    return len(BUCKET_COLS) - 1


def _find_header_row(path: str, marker: str = _HEADER_MARKER,
                      default: int = 13, search_range: int = 25) -> int:
    """Scan the first `search_range` rows for the one containing `marker`
    as a cell value, returning its 0-indexed row number (suitable for
    pandas' `header=` argument). Falls back to `default` if not found -
    this export's header has reliably been at row 14 (index 13)."""
    probe = pd.read_excel(path, header=None, nrows=search_range, engine="openpyxl")
    for i in range(len(probe)):
        if any(marker == str(v).strip() for v in probe.iloc[i].tolist()):
            return i
    return default


def read_ageing_file(path: str, log_q=None) -> pd.DataFrame:
    """Read the Aging export, promote the real header row, and drop the
    metadata/blank/Grand-Total rows around it."""
    if log_q:
        log_q.put(("info", f"Reading {Path(path).name} ..."))
    try:
        header_row = _find_header_row(path)
        df = pd.read_excel(path, header=header_row, engine="openpyxl")
    except Exception as exc:
        raise JobUserError(
            "Could not open the uploaded file as an Excel workbook. Please check it isn't "
            "corrupted and that it matches the expected Aging export format."
        ) from exc

    df.columns = [str(c).strip() if not pd.isna(c) else "" for c in df.columns]

    cust_col = _find_col(df.columns, ("customer", "name")) or "Customer Name"
    if cust_col not in df.columns:
        raise JobUserError(
            f"Could not find the expected '{_HEADER_MARKER}' column after reading. "
            f"Columns found: {', '.join(str(c) for c in list(df.columns)[:10])}."
        )

    before = len(df)
    cleaned = df[cust_col].astype(str).str.strip()
    is_grand_total = cleaned.str.lower().str.rstrip(":") == "grand total"
    df = df[~is_grand_total].copy()
    df = df.dropna(subset=[cust_col]).reset_index(drop=True)
    df = df.dropna(how="all").reset_index(drop=True)

    if log_q:
        log_q.put(("ok", f"Read {before:,} raw rows -> {len(df):,} data rows after cleanup"))
    if df.empty:
        raise JobUserError("No data rows were found in the uploaded Aging export.")
    return df


def missing_location_mappings(df: pd.DataFrame, location_map: dict[str, str]) -> list[str]:
    """Distinct raw 'Location Name' values (uppercased) with no entry in
    location_map."""
    loc_name_col = _find_col(df.columns, _LOCATION_NAME_TOKENS)
    if not loc_name_col:
        return []
    raw = df[loc_name_col].astype(str).str.strip().str.upper()
    uniq = sorted(v for v in raw.unique() if v and v != "NAN")
    return [v for v in uniq if v not in location_map]


def missing_incharge_mappings(locations: list[str], incharge_map: dict[str, str]) -> list[str]:
    uniq = sorted({loc for loc in locations if loc})
    return [loc for loc in uniq if loc not in incharge_map]


def add_location_column(df: pd.DataFrame, location_map: dict[str, str]) -> pd.DataFrame:
    """Insert the mapped 'Location' column immediately after 'Location Name'."""
    df = df.copy()
    loc_name_col = _find_col(df.columns, _LOCATION_NAME_TOKENS)
    if not loc_name_col:
        raise JobUserError("Could not find a 'Location Name' column in the uploaded file.")

    raw_upper = df[loc_name_col].astype(str).str.strip().str.upper()
    df[LOCATION_COL] = raw_upper.map(location_map).fillna("")

    cols = list(df.columns)
    cols.remove(LOCATION_COL)
    cols.insert(cols.index(loc_name_col) + 1, LOCATION_COL)
    return df[cols]


def recompute_ageing_buckets(df: pd.DataFrame, as_on_date: _dt.date, log_q=None) -> pd.DataFrame:
    """Overwrite Days O/S and every bucket column from
    (as_on_date - Invoice/Receipt Date).days, ignoring whatever the source
    file's own bucket columns held (they're frozen to the ERP export's own
    "As of Date")."""
    df = df.copy()
    date_col = _find_col(df.columns, _INVOICE_DATE_TOKENS)
    outstanding_col = _find_col(df.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    if not date_col or not outstanding_col:
        raise JobUserError(
            "Could not find the 'Invoice/ Receipt Date' and/or 'Accounted Outstanding' "
            "columns in the uploaded file."
        )

    for col in BUCKET_COLS:
        if col not in df.columns:
            df[col] = 0.0

    invoice_dates = pd.to_datetime(df[date_col], errors="coerce")
    as_on_ts = pd.Timestamp(as_on_date)
    days_os = (as_on_ts - invoice_dates).dt.days
    outstanding = pd.to_numeric(df[outstanding_col], errors="coerce").fillna(0.0)

    unparseable = int(invoice_dates.isna().sum())
    if unparseable and log_q:
        log_q.put(("warn", f"{unparseable:,} row(s) have no readable invoice date - "
                            "Days O/S and buckets left blank for those rows"))

    df[_DAYS_OS_COL] = days_os
    valid = days_os.notna()

    # Vectorized equivalent of `days_os.apply(_bucket_index)` + a per-bucket
    # loop: a row-wise Python callback and repeated boolean-mask passes are
    # both far too slow at 200k+ rows, so this does the same one-bucket-per-
    # row assignment with a single numpy fancy-index write instead.
    edges_arr = np.asarray(_BUCKET_EDGES)
    days_arr = days_os.fillna(edges_arr[-1] + 1).to_numpy()
    bucket_idx = np.searchsorted(edges_arr, days_arr, side="left")

    bucket_matrix = np.zeros((len(df), len(BUCKET_COLS)), dtype=float)
    valid_arr = valid.to_numpy()
    row_idx = np.nonzero(valid_arr)[0]
    bucket_matrix[row_idx, bucket_idx[row_idx]] = outstanding.to_numpy()[row_idx]
    bucket_frame = pd.DataFrame(bucket_matrix, index=df.index, columns=BUCKET_COLS)

    for col in BUCKET_COLS:
        df[col] = bucket_frame[col].where(valid, other=pd.NA)
    df[_DAYS_OS_COL] = df[_DAYS_OS_COL].where(valid, other=pd.NA)

    if log_q:
        log_q.put(("ok", f"Ageing buckets recomputed as on {as_on_date.strftime('%d-%b-%Y')}"))
    return df


# ── pivots ────────────────────────────────────────────────────────────────

def build_below_1k_pivot(df: pd.DataFrame, incharge_map: dict[str, str]) -> pd.DataFrame:
    """Location-wise pivot of rows with 0 < Accounted Outstanding < 1,000."""
    outstanding_col = _find_col(df.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    outstanding = pd.to_numeric(df[outstanding_col], errors="coerce").fillna(0.0)
    mask = (outstanding > 0) & (outstanding < 1000)
    subset = df[mask].copy()
    subset["_outstanding"] = outstanding[mask]

    if subset.empty:
        pivot = pd.DataFrame(columns=[LOCATION_COL, "_outstanding", *BUCKET_COLS])
    else:
        pivot = (
            subset.groupby(LOCATION_COL)[["_outstanding", *BUCKET_COLS]]
            .sum()
            .reset_index()
        )

    pivot = pivot.rename(columns={"_outstanding": "Total O/S"})
    for col in BUCKET_COLS:
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot["Above 30 days"] = pivot["Total O/S"] - pivot["0-30 Days"] - pivot["31-60 Days"]
    pivot["Account Incharges"] = pivot[LOCATION_COL].map(lambda loc: incharge_map.get(loc, ""))
    pivot = pivot.sort_values(LOCATION_COL, key=lambda s: s.str.lower()).reset_index(drop=True)

    return pivot[[LOCATION_COL, "Account Incharges", "Total O/S", *BUCKET_COLS, "Above 30 days"]]


def build_untagged_detail(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where Type = Receipt and Accounted Outstanding < 0 - same
    columns as the Ageing sheet."""
    type_col = _find_col(df.columns, _TYPE_TOKENS)
    outstanding_col = _find_col(df.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    if not type_col or not outstanding_col:
        raise JobUserError(
            "Could not find the 'Type (Transaction/Receipt)' and/or 'Accounted Outstanding' "
            "columns in the uploaded file."
        )
    is_receipt = df[type_col].astype(str).str.strip().str.lower().str.startswith("receipt")
    outstanding = pd.to_numeric(df[outstanding_col], errors="coerce").fillna(0.0)
    mask = is_receipt & (outstanding < 0)
    return df[mask].reset_index(drop=True)


def build_untagged_summary(df_untagged: pd.DataFrame, incharge_map: dict[str, str]) -> pd.DataFrame:
    """Location-wise pivot of the Untagged Detailed Ageing rows."""
    outstanding_col = _find_col(df_untagged.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    subset = df_untagged.copy()
    subset["_outstanding"] = pd.to_numeric(subset[outstanding_col], errors="coerce").fillna(0.0)

    if subset.empty:
        pivot = pd.DataFrame(columns=[LOCATION_COL, "_outstanding", *BUCKET_COLS])
    else:
        pivot = (
            subset.groupby(LOCATION_COL)[["_outstanding", *BUCKET_COLS]]
            .sum()
            .reset_index()
        )

    pivot = pivot.rename(columns={"_outstanding": "Accounted Outstanding"})
    for col in BUCKET_COLS:
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot["Above 30 Days"] = pivot["Accounted Outstanding"] - pivot["0-30 Days"]
    pivot["Account Incharges"] = pivot[LOCATION_COL].map(lambda loc: incharge_map.get(loc, ""))
    pivot = pivot.sort_values("Accounted Outstanding").reset_index(drop=True)

    return pivot[[LOCATION_COL, "Account Incharges", "Accounted Outstanding", *BUCKET_COLS, "Above 30 Days"]]


# ══════════════════════════════════════════════════════════════════════════
#  EXCEL FORMATTING
# ══════════════════════════════════════════════════════════════════════════

_CURRENCY_MARKERS = ("outstanding", "invoice", "amount", "limit", "o/s", "days")
_HDR_BG   = "1F3864"
_TITLE_BG = "152748"
_ROW_ODD  = "FFFFFF"
_ROW_EVEN = "EDF2FB"


def _thin_border() -> Border:
    s = Side(style="thin", color="D0D8E4")
    return Border(left=s, right=s, top=s, bottom=s)


def _is_numeric_col(col_name: str) -> bool:
    low = str(col_name).lower()
    if col_name in BUCKET_COLS or col_name == _DAYS_OS_COL:
        return True
    return any(m in low for m in _CURRENCY_MARKERS)


# Fixed named styles for _write_detail_sheet, registered once per workbook.
# Assigning `cell.style = "some_name"` is a cheap dict lookup by string; the
# original code assigned shared Font/Fill/Border/Alignment *objects* to each
# cell instead, which sounds equally cheap but isn't - openpyxl deduplicates
# style objects into a workbook-level table, so every `cell.font = obj`
# triggers a deep equality/hash comparison of `obj` against everything
# already registered there. Profiling a 50k-row slice of this sheet showed
# that machinery (openpyxl.descriptors.serialisable Serialisable.__eq__/
# __hash__, called ~80M times) accounted for 87% of total write time -
# dwarfing the actual XML writing. Pre-registering a small fixed set of
# named styles and referencing them by name avoids that entirely.
_STYLE_HDR          = "uir_header"
_STYLE_NUM_INT_ODD  = "uir_num_int_odd"
_STYLE_NUM_INT_EVEN = "uir_num_int_even"
_STYLE_NUM_DEC_ODD  = "uir_num_dec_odd"
_STYLE_NUM_DEC_EVEN = "uir_num_dec_even"
_STYLE_TXT_ODD      = "uir_txt_odd"
_STYLE_TXT_EVEN     = "uir_txt_even"
_STYLE_DATE_ODD     = "uir_date_odd"
_STYLE_DATE_EVEN    = "uir_date_even"


def _ensure_detail_named_styles(wb: Workbook) -> None:
    """Register the named styles above on `wb`, once. Both detail sheets
    (Ageing, Untagged Detailed Ageing) share the same workbook, so the
    second call is a no-op."""
    if _STYLE_HDR in wb.named_styles:
        return

    bdr = _thin_border()
    wb.add_named_style(NamedStyle(
        name=_STYLE_HDR,
        font=Font(name="Segoe UI", bold=True, size=10, color="FFFFFF"),
        fill=PatternFill("solid", fgColor=_HDR_BG),
        border=bdr,
        alignment=Alignment(horizontal="center", vertical="center", wrap_text=True),
    ))

    num_font = Font(name="Consolas", size=10)
    align_right = Alignment(horizontal="right", vertical="center")
    for name, fill, number_format in (
        (_STYLE_NUM_INT_ODD,  PatternFill("solid", fgColor=_ROW_ODD),  "#,##0"),
        (_STYLE_NUM_INT_EVEN, PatternFill("solid", fgColor=_ROW_EVEN), "#,##0"),
        (_STYLE_NUM_DEC_ODD,  PatternFill("solid", fgColor=_ROW_ODD),  "#,##0.00"),
        (_STYLE_NUM_DEC_EVEN, PatternFill("solid", fgColor=_ROW_EVEN), "#,##0.00"),
    ):
        wb.add_named_style(NamedStyle(name=name, font=num_font, fill=fill,
                                       border=bdr, alignment=align_right,
                                       number_format=number_format))

    txt_font = Font(name="Segoe UI", size=10)
    align_left = Alignment(horizontal="left", vertical="center", indent=1)
    for name, fill, number_format in (
        (_STYLE_TXT_ODD,   PatternFill("solid", fgColor=_ROW_ODD),  "General"),
        (_STYLE_TXT_EVEN,  PatternFill("solid", fgColor=_ROW_EVEN), "General"),
        (_STYLE_DATE_ODD,  PatternFill("solid", fgColor=_ROW_ODD),  "DD-MMM-YYYY"),
        (_STYLE_DATE_EVEN, PatternFill("solid", fgColor=_ROW_EVEN), "DD-MMM-YYYY"),
    ):
        wb.add_named_style(NamedStyle(name=name, font=txt_font, fill=fill,
                                       border=bdr, alignment=align_left,
                                       number_format=number_format))


def _write_detail_sheet(wb: Workbook, title: str, df: pd.DataFrame, tab_color: str,
                        log_q=None, progress_cb=None, base: float = 0.0, span: float = 0.3) -> None:
    """Write a full-detail sheet (Ageing / Untagged Detailed Ageing) - every
    original column plus Location. `wb` is a write-only workbook: rows are
    streamed straight to disk via ws.append() instead of building a normal
    openpyxl Cell object per cell that stays resident in memory - at 200k+
    rows x ~25 columns that's several million long-lived cell objects, which
    is what actually made this sheet slow (and memory-hungry) to write."""
    ws = wb.create_sheet(title=title)
    ws.sheet_properties.tabColor = tab_color
    ws.sheet_view.showGridLines = False
    _ensure_detail_named_styles(wb)

    cols = list(df.columns)
    n_cols = len(cols)
    n_rows = len(df)
    report_row = row_progress_reporter(progress_cb, base=base, span=span, total_rows=n_rows)

    ws.row_dimensions[1].height = 32
    header_cells = []
    col_max_len = []
    for col_name in cols:
        label = str(col_name).strip()
        c = WriteOnlyCell(ws, value=label)
        c.style = _STYLE_HDR
        header_cells.append(c)
        col_max_len.append(len(label))
    ws.append(header_cells)

    numeric_cols = [_is_numeric_col(c) for c in cols]
    days_os_idx  = cols.index(_DAYS_OS_COL) if _DAYS_OS_COL in cols else -1

    # Column widths only ever sampled the first 200 written rows - track that
    # inline while building rows instead of a second pass reading cells back
    # (which write-only mode can't do anyway, since rows aren't kept once
    # appended).
    WIDTH_SAMPLE_ROWS = 200

    for ri, row_vals in enumerate(df.itertuples(index=False), 2):
        is_even_row = (ri % 2 == 0)
        track_width = (ri - 2) < WIDTH_SAMPLE_ROWS
        row_cells = []
        for ci, val in enumerate(row_vals, 1):
            c = WriteOnlyCell(ws)
            is_blank = val is None or (isinstance(val, float) and pd.isna(val))
            if numeric_cols[ci - 1]:
                is_days = (ci - 1) == days_os_idx
                if is_blank:
                    c.value = None
                    disp = ""
                else:
                    try:
                        c.value = int(val) if is_days else float(val)
                    except (TypeError, ValueError):
                        c.value = val
                    disp = str(c.value)
                if is_days:
                    c.style = _STYLE_NUM_INT_EVEN if is_even_row else _STYLE_NUM_INT_ODD
                else:
                    c.style = _STYLE_NUM_DEC_EVEN if is_even_row else _STYLE_NUM_DEC_ODD
            else:
                if is_blank:
                    c.value = ""
                    disp = ""
                    c.style = _STYLE_TXT_EVEN if is_even_row else _STYLE_TXT_ODD
                elif isinstance(val, _dt.datetime):
                    c.value = val
                    c.style = _STYLE_DATE_EVEN if is_even_row else _STYLE_DATE_ODD
                    disp = val.strftime("%d-%b-%Y")
                else:
                    c.value = str(val)
                    disp = c.value
                    c.style = _STYLE_TXT_EVEN if is_even_row else _STYLE_TXT_ODD
            row_cells.append(c)
            if track_width and len(disp) > col_max_len[ci - 1]:
                col_max_len[ci - 1] = len(disp)
        ws.append(row_cells)
        report_row(ri - 1)

    for ci in range(1, n_cols + 1):
        col_letter = get_column_letter(ci)
        ws.column_dimensions[col_letter].width = min(max(col_max_len[ci - 1] + 3, 10), 45)

    ws.freeze_panes = "A2"
    if n_rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{n_rows + 1}"


def _write_pivot_sheet(wb: Workbook, title: str, df: pd.DataFrame, tab_color: str,
                       location_col_header: str, amount_col: str, above_30_formula) -> None:
    """Write a Location-wise pivot sheet (Below 1k / Untagged Summary) with
    a live-SUBTOTAL Grand Total row.

    above_30_formula(amount_ref, bucket_refs) -> the Excel formula string
    for that sheet's own "Above 30 Days" column, built from cell refs so it
    (and the Grand Total row's own copy of it) recalculates live in Excel.
    """
    ws = wb.create_sheet(title=title)
    ws.sheet_properties.tabColor = tab_color
    ws.sheet_view.showGridLines = False

    cols = [LOCATION_COL, "Account Incharges", amount_col, *BUCKET_COLS, "Above 30 Days"]
    header_labels = {
        LOCATION_COL: location_col_header,
        "361-999999 Days ": "361-99999" if amount_col == "Total O/S" else "361+",
    }
    bdr = _thin_border()
    n_cols = len(cols)
    n_rows = len(df)

    hdr_font  = Font(name="Segoe UI", bold=True, size=10, color="FFFFFF")
    hdr_fill  = PatternFill("solid", fgColor=_HDR_BG)
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    header_cells = []
    for col_name in cols:
        label = header_labels.get(col_name, col_name.strip())
        c = WriteOnlyCell(ws, value=label)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = hdr_align
        c.border = bdr
        header_cells.append(c)
    ws.append(header_cells)

    txt_font    = Font(name="Segoe UI", size=10)
    num_font    = Font(name="Consolas", size=10)
    fill_odd    = PatternFill("solid", fgColor=_ROW_ODD)
    fill_even   = PatternFill("solid", fgColor=_ROW_EVEN)
    align_right = Alignment(horizontal="right", vertical="center")
    align_left  = Alignment(horizontal="left", vertical="center", indent=1)

    amount_col_idx = cols.index(amount_col) + 1
    bucket_first_idx = amount_col_idx + 1
    bucket_last_idx = bucket_first_idx + len(BUCKET_COLS) - 1
    above30_idx = n_cols

    # Plain positional tuples (name=None) rather than itertuples' namedtuple
    # form - several of these column names ("Account Incharges", "Above 30
    # Days") aren't valid Python identifiers, and relying on pandas' name-
    # mangling for them would be fragile.
    data_cols = [LOCATION_COL, "Account Incharges", amount_col, *BUCKET_COLS]
    for ri, row_vals in enumerate(df[data_cols].itertuples(index=False, name=None), 2):
        row_fill = fill_even if ri % 2 == 0 else fill_odd
        row_cells = []
        for col_name, val in zip(data_cols, row_vals):
            c = WriteOnlyCell(ws)
            c.border = bdr
            c.fill = row_fill
            if col_name in (LOCATION_COL, "Account Incharges"):
                c.value = str(val) if val else ""
                c.font = txt_font
                c.alignment = align_left
            else:
                c.font = num_font
                c.alignment = align_right
                c.number_format = "#,##0.00"
                c.value = None if val is None or pd.isna(val) else float(val)
            row_cells.append(c)

        report_amount_ref = f"{get_column_letter(amount_col_idx)}{ri}"
        bucket_refs = [f"{get_column_letter(i)}{ri}" for i in range(bucket_first_idx, bucket_last_idx + 1)]
        above_cell = WriteOnlyCell(ws, value=above_30_formula(report_amount_ref, bucket_refs))
        above_cell.font = num_font
        above_cell.alignment = align_right
        above_cell.number_format = "#,##0.00"
        above_cell.border = bdr
        above_cell.fill = row_fill
        row_cells.append(above_cell)

        ws.append(row_cells)

    grand_row = n_rows + 2
    gt_font = Font(name="Segoe UI", bold=True, size=10, color="FFFFFF")
    gt_fill = PatternFill("solid", fgColor=_HDR_BG)
    ws.row_dimensions[grand_row].height = 22

    grand_cells = []
    gt = WriteOnlyCell(ws, value="Grand Total")
    gt.font = gt_font
    gt.fill = gt_fill
    gt.alignment = align_left
    gt.border = bdr
    grand_cells.append(gt)

    inc_cell = WriteOnlyCell(ws, value="")
    inc_cell.font = gt_font
    inc_cell.fill = gt_fill
    inc_cell.border = bdr
    grand_cells.append(inc_cell)

    for ci in range(3, n_cols):  # amount col + bucket cols (excludes Above 30 Days)
        col_letter = get_column_letter(ci)
        c = WriteOnlyCell(ws)
        if n_rows:
            c.value = f"=SUBTOTAL(9,{col_letter}2:{col_letter}{n_rows + 1})"
        else:
            c.value = 0
        c.number_format = "#,##0.00"
        c.font = gt_font
        c.fill = gt_fill
        c.alignment = align_right
        c.border = bdr
        grand_cells.append(c)

    grand_amount_ref = f"{get_column_letter(amount_col_idx)}{grand_row}"
    grand_bucket_refs = [f"{get_column_letter(i)}{grand_row}" for i in range(bucket_first_idx, bucket_last_idx + 1)]
    grand_above = WriteOnlyCell(ws, value=above_30_formula(grand_amount_ref, grand_bucket_refs))
    grand_above.font = gt_font
    grand_above.fill = gt_fill
    grand_above.alignment = align_right
    grand_above.number_format = "#,##0.00"
    grand_above.border = bdr
    grand_cells.append(grand_above)

    ws.append(grand_cells)

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 18
    for ci in range(3, n_cols + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 15

    ws.freeze_panes = "A2"
    if n_rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{n_rows + 1}"


def write_report(
    df_ageing: pd.DataFrame,
    df_below_1k: pd.DataFrame,
    df_untagged_detail: pd.DataFrame,
    df_untagged_summary: pd.DataFrame,
    output_path: str | Path,
    as_on_date: _dt.date,
    log_q=None,
    progress_cb=None,
) -> None:
    """Write the 4-sheet workbook, in the required order:
    Untagged Summary, Untagged Detailed Ageing, Ageing, Below 1k.

    Built as a write-only workbook: every sheet is streamed row-by-row via
    ws.append() rather than kept as normal openpyxl Cell objects, which is
    what makes this practical at 200k+ rows (see _write_detail_sheet)."""
    wb = Workbook(write_only=True)

    if progress_cb:
        progress_cb(0.55, "Writing Untagged Summary...")
    _write_pivot_sheet(
        wb, "Untagged Summary", df_untagged_summary, "6A1B9A",
        location_col_header="Location Name", amount_col="Accounted Outstanding",
        above_30_formula=lambda amount_ref, bucket_refs: f"={amount_ref}-{bucket_refs[0]}",
    )

    if progress_cb:
        progress_cb(0.60, "Writing Untagged Detailed Ageing...")
    _write_detail_sheet(
        wb, "Untagged Detailed Ageing", df_untagged_detail, "37474F",
        log_q=log_q, progress_cb=progress_cb, base=0.60, span=0.05,
    )

    if progress_cb:
        progress_cb(0.65, "Writing Ageing (full detail)...")
    _write_detail_sheet(
        wb, "Ageing", df_ageing, "1F3864",
        log_q=log_q, progress_cb=progress_cb, base=0.65, span=0.30,
    )

    if progress_cb:
        progress_cb(0.95, "Writing Below 1k...")
    _write_pivot_sheet(
        wb, "Below 1k", df_below_1k, "00897B",
        location_col_header="Location Name", amount_col="Total O/S",
        above_30_formula=lambda amount_ref, bucket_refs: f"={amount_ref}-{bucket_refs[0]}-{bucket_refs[1]}",
    )

    output_path = Path(output_path)
    wb.save(output_path)
    if log_q:
        log_q.put(("success", f"Saved -> {output_path}"))
    if progress_cb:
        progress_cb(1.0, "Report ready")
