"""Untagged Invoices Report Generator — processing pipeline.

Single input: the open AR ageing, fetched live from the Oracle ERP
(``fetch_ageing_from_erp``) - there is no file to upload. The query returns
the data under the same column names the old Oracle "Aging" export used, so
everything below is unchanged. Produces one workbook with 5 sheets, in this
fixed order:

  1. Untagged Summary    - Location-wise pivot of the Untagged Ageing rows,
                            sorted by Accounted Outstanding ascending
  2. Untagged Ageing      - rows from Ageing where Type = Transactions and
                            Accounted Outstanding < 0 (keeps the Type column)
  3. Below 1k Summary     - Location-wise pivot of the Below 1k Ageing rows,
                            sorted by Total O/S descending
  4. Below 1k Ageing      - rows from Ageing where Type = Transactions and
                            0 < Accounted Outstanding < 1,000 (keeps the
                            Type column)
  5. Ageing               - the full input, with a "Location" column added
                            right after "Location Name" (mapped via the
                            centralized Location Name -> Location mapping)
                            and ageing buckets recomputed; every row, both
                            Transactions and Receipts, unfiltered

Both "Ageing" detail sheets (Untagged Ageing, Below 1k Ageing) are
restricted to Type = Transactions - Receipts rows are never untagged or
below-1k candidates, only the raw "Ageing" sheet itself carries them.

Ageing buckets (Days O/S, 0-15 Days, 16-30 Days, ...) are always recomputed
from (Invoice/ Receipt Date, as_on_date) rather than taken from the ERP
query's own 0-30 / 31-60 ... buckets, which use different ranges. The report
always runs on the live ERP data, so as_on_date is simply today.

In the written workbook an empty cell or a zero shows "-".
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, NamedStyle, PatternFill, Border, Side
from openpyxl.cell import WriteOnlyCell
from openpyxl.utils import get_column_letter

from app.jobs import JobUserError
from app.services import tabular_input
from app.services.erp_ageing import OracleConfig, fetch_ageing_from_erp  # noqa: F401 - used by the router
from app.services.report_dash import DASH as _DASH, FMT_DEC as _FMT_DEC, FMT_INT as _FMT_INT, is_blank as _is_blank
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
    "0-15 Days", "16-30 Days", "31-60 Days", "61-90 Days", "91-120 Days",
    "121-150 Days", "151-180 Days", "181-360 Days", "360+ Days",
]
_BUCKET_EDGES = (15, 30, 60, 90, 120, 150, 180, 360)  # last bucket = anything above 360

# Matches any "<number>-<number> Days" / "<number>+ Days"-shaped header,
# regardless of its exact wording - used to strip the source file's own
# (frozen) bucket columns before recomputing, see recompute_ageing_buckets.
_BUCKET_LIKE_RE = re.compile(r"^\d+\s*[-+]")


def _looks_like_bucket_col(name) -> bool:
    s = str(name).strip()
    return bool(_BUCKET_LIKE_RE.match(s)) and "day" in s.lower()


LOCATION_COL = "Location"
_LAKH = 100_000
COUNT_COL = "Count of Invoices"


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


_REQUIRED = (
    # (what the user sees, tokens that identify the column)
    ("Customer Name", ("customer", "name")),
    ("Location Name", _LOCATION_NAME_TOKENS),
    ("Invoice/ Receipt Date", _INVOICE_DATE_TOKENS),
    ("Accounted Outstanding", _ACCOUNTED_OUTSTANDING_TOKENS),
    ("Type (Transaction/Receipt)", _TYPE_TOKENS),
)


def _sample(values, limit: int = 8) -> str:
    shown = [str(v) for v in values if str(v).strip() and str(v).strip().lower() != "nan"][:limit]
    return ", ".join(shown) if shown else "(none)"


def _locate_header_row(grid: pd.DataFrame) -> int:
    """The header row of the Aging export: the row that holds 'Customer Name'
    (normally row 14); failing that, the row in the first 40 that looks most
    like it (matches the most required columns)."""
    exact = tabular_input.find_header_row(grid, _HEADER_MARKER, search_range=40)
    if exact is not None:
        return exact
    best_row, best_hits = None, 0
    for i in range(min(40, len(grid))):
        cols = [str(v).strip() for v in grid.iloc[i].tolist() if v is not None and not pd.isna(v)]
        hits = sum(1 for _, tokens in _REQUIRED if _find_col(cols, tokens) is not None)
        if hits > best_hits:
            best_row, best_hits = i, hits
    if best_row is not None and best_hits >= 3:
        return best_row
    first_cells = [str(v).strip() for v in grid.iloc[0].tolist() if v is not None and not pd.isna(v)]
    raise JobUserError(
        "This doesn't look like the Aging export: no header row containing 'Customer Name' was "
        f"found in its first 40 rows. The first row reads: {_sample(first_cells)}. "
        "Please upload the Aging report exactly as exported from the ERP."
    )


def _unique_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in columns:
        if name in seen:
            seen[name] += 1
            out.append(f"{name}.{seen[name]}")
        else:
            seen[name] = 0
            out.append(name)
    return out


def read_ageing_file(path: str, log_q=None) -> pd.DataFrame:
    """Read the Aging export (any Excel flavour, ERP HTML, or CSV), promote
    the real header row, drop the metadata/blank/Grand-Total rows around it,
    and check that every column the report needs is there and holds usable
    values. Every problem is reported in plain words."""
    shown = tabular_input._name(path)
    if log_q:
        log_q.put(("info", f"Reading {shown} ..."))
    grid = tabular_input.read_grid(path, log_q=log_q)
    header_row = _locate_header_row(grid)
    df = tabular_input.promote_header(grid, header_row)
    df.columns = _unique_columns([c if c else f"Column {i + 1}" for i, c in enumerate(df.columns)])

    missing = [label for label, tokens in _REQUIRED if _find_col(df.columns, tokens) is None]
    if missing:
        raise JobUserError(
            f"The uploaded file is missing required column(s): {', '.join(missing)}. "
            f"Columns found in it: {_sample(df.columns, 25)}. "
            "Please upload the Aging report exactly as exported from the ERP (no columns removed or renamed)."
        )

    cust_col = _find_col(df.columns, ("customer", "name"))
    before = len(df)
    cleaned = df[cust_col].astype(str).str.strip()
    is_grand_total = cleaned.str.lower().str.rstrip(":") == "grand total"
    df = df[~is_grand_total].copy()
    df = df.dropna(subset=[cust_col]).reset_index(drop=True)
    df = df.dropna(how="all").reset_index(drop=True)
    df = df[df[cust_col].astype(str).str.strip().ne("")].reset_index(drop=True)

    if log_q:
        log_q.put(("ok", f"Read {before:,} raw rows -> {len(df):,} data rows after cleanup"))
    if df.empty:
        raise JobUserError(
            "The Aging export has its header row but no data rows beneath it. "
            "Please check the report was run for the right period and exported with data."
        )

    # values: amounts must be numbers, dates must be dates, locations present
    outstanding_col = _find_col(df.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    amounts, bad_amounts = tabular_input.clean_numeric(df[outstanding_col])
    if amounts.notna().sum() == 0:
        raise JobUserError(
            f"Every value in the '{outstanding_col}' column is blank or not a number "
            f"(for example: {_sample(df[outstanding_col].dropna().astype(str).unique(), 5)}). "
            "Please check the right column is in that position and the file wasn't altered."
        )
    if bad_amounts and log_q:
        log_q.put(("warn", f"'{outstanding_col}' has value(s) that are not numbers and are treated "
                           f"as blank: {', '.join(bad_amounts)}"))
    df[outstanding_col] = amounts

    date_col = _find_col(df.columns, _INVOICE_DATE_TOKENS)
    dates, bad_dates = tabular_input.parse_dates(df[date_col])
    if dates.notna().sum() == 0:
        raise JobUserError(
            f"No value in the '{date_col}' column could be read as a date "
            f"(for example: {_sample(df[date_col].dropna().astype(str).unique(), 5)}). "
            "Dates should look like 31-Aug-2026 or 31/08/2026."
        )
    if bad_dates and log_q:
        log_q.put(("warn", f"'{date_col}' has value(s) that are not dates (their Days O/S and "
                           f"buckets stay blank): {', '.join(bad_dates)}"))
    df[date_col] = dates

    location_col = _find_col(df.columns, _LOCATION_NAME_TOKENS)
    if df[location_col].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""}).eq("").all():
        raise JobUserError(
            f"The '{location_col}' column is empty for every row, so no location can be worked out. "
            "Please check the Aging export was run with locations."
        )

    type_col = _find_col(df.columns, _TYPE_TOKENS)
    # blank cells would otherwise surface as NA inside the row filters below
    df[type_col] = df[type_col].fillna("")
    df[location_col] = df[location_col].fillna("")
    types = df[type_col].astype(str).str.strip().str.lower()
    if not types.str.startswith("transaction").any() and log_q:
        log_q.put(("warn", f"No row has '{type_col}' = Transaction (values found: "
                           f"{_sample(df[type_col].dropna().astype(str).unique(), 5)}) - the "
                           "Untagged and Below 1k sheets will be empty."))
    return df


def missing_location_mappings(df: pd.DataFrame, location_map: dict[str, str]) -> list[str]:
    """Distinct raw 'Location Name' values (uppercased) with no entry in
    location_map."""
    loc_name_col = _find_col(df.columns, _LOCATION_NAME_TOKENS)
    if not loc_name_col:
        return []
    raw = df[loc_name_col].fillna("").astype(str).str.strip().str.upper()
    uniq = sorted(v for v in raw.unique() if v and v not in ("NAN", "NONE"))
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

    raw_upper = df[loc_name_col].fillna("").astype(str).str.strip().str.upper()
    df[LOCATION_COL] = raw_upper.map(location_map).fillna("")

    cols = list(df.columns)
    cols.remove(LOCATION_COL)
    cols.insert(cols.index(loc_name_col) + 1, LOCATION_COL)
    return df[cols]


def recompute_ageing_buckets(df: pd.DataFrame, as_on_date: _dt.date, log_q=None) -> pd.DataFrame:
    """Overwrite Days O/S and every bucket column from
    (as_on_date - Invoice/Receipt Date).days, ignoring whatever the source
    file's own bucket columns held (they're frozen to the ERP export's own
    "As of Date").

    The source file's own bucket columns are dropped and replaced rather
    than overwritten in place by exact name match: the ERP export's last
    bucket header ("361-999999 Days ") carries a trailing space that
    read_ageing_file's own column-name stripping removes, so an exact-name
    overwrite silently left that untouched raw column behind alongside a
    freshly computed one of an almost-identical name - the sheet then
    showed that bucket column twice. Matching any "<number>-Days"-shaped
    header instead avoids relying on the exact wording of either the source
    file's or this module's own bucket names."""
    df = df.copy()
    date_col = _find_col(df.columns, _INVOICE_DATE_TOKENS)
    outstanding_col = _find_col(df.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    if not date_col or not outstanding_col:
        raise JobUserError(
            "Could not find the 'Invoice/ Receipt Date' and/or 'Accounted Outstanding' "
            "columns in the uploaded file."
        )

    stale_bucket_cols = [c for c in df.columns if _looks_like_bucket_col(c)]
    df = df.drop(columns=stale_bucket_cols)
    insert_at = (df.columns.get_loc(_DAYS_OS_COL) + 1) if _DAYS_OS_COL in df.columns else len(df.columns)

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

    for offset, col in enumerate(BUCKET_COLS):
        df.insert(insert_at + offset, col, bucket_frame[col].where(valid, other=pd.NA))
    df[_DAYS_OS_COL] = df[_DAYS_OS_COL].where(valid, other=pd.NA)

    if log_q:
        log_q.put(("ok", f"Ageing buckets recomputed as on {as_on_date.strftime('%d-%b-%Y')}"))
    return df


# ── pivots ────────────────────────────────────────────────────────────────

def _invoice_counts(subset: pd.DataFrame, pivot: pd.DataFrame) -> pd.Series:
    """Number of Ageing rows (one per invoice) behind each pivot Location."""
    per_location = subset.groupby(LOCATION_COL).size()
    return pivot[LOCATION_COL].map(per_location).fillna(0).astype(int)


def build_below_1k_ageing(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where Type = Transactions and 0 < Accounted Outstanding < 1,000 -
    same columns as the Ageing sheet, Type column included. Feeds the Below
    1k Summary pivot and is also written as its own "Below 1k Ageing"
    detail sheet."""
    type_col = _find_col(df.columns, _TYPE_TOKENS)
    outstanding_col = _find_col(df.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    if not type_col or not outstanding_col:
        raise JobUserError(
            "Could not find the 'Type (Transaction/Receipt)' and/or 'Accounted Outstanding' "
            "columns in the uploaded file."
        )
    is_transaction = df[type_col].astype(str).str.strip().str.lower().str.startswith("transaction")
    outstanding = pd.to_numeric(df[outstanding_col], errors="coerce").fillna(0.0)
    mask = is_transaction & (outstanding > 0) & (outstanding < 1000)
    return df[mask].reset_index(drop=True)


def build_below_1k_summary(df_below_1k_ageing: pd.DataFrame, incharge_map: dict[str, str]) -> pd.DataFrame:
    """Location-wise pivot of the Below 1k Ageing rows, sorted by Total O/S
    descending."""
    outstanding_col = _find_col(df_below_1k_ageing.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    subset = df_below_1k_ageing.copy()
    subset["_outstanding"] = pd.to_numeric(subset[outstanding_col], errors="coerce").fillna(0.0)

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
    pivot[COUNT_COL] = _invoice_counts(subset, pivot)
    pivot["Above 30 days"] = pivot["Total O/S"] - pivot[BUCKET_COLS[0]] - pivot[BUCKET_COLS[1]]
    pivot["Account Incharges"] = pivot[LOCATION_COL].map(lambda loc: incharge_map.get(loc, ""))
    pivot = pivot.sort_values("Total O/S", ascending=False).reset_index(drop=True)

    return pivot[[LOCATION_COL, "Account Incharges", COUNT_COL, "Total O/S", *BUCKET_COLS, "Above 30 days"]]


def build_untagged_ageing(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where Type = Transactions and Accounted Outstanding < 0 - same
    columns as the Ageing sheet, Type column included."""
    type_col = _find_col(df.columns, _TYPE_TOKENS)
    outstanding_col = _find_col(df.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    if not type_col or not outstanding_col:
        raise JobUserError(
            "Could not find the 'Type (Transaction/Receipt)' and/or 'Accounted Outstanding' "
            "columns in the uploaded file."
        )
    is_transaction = df[type_col].astype(str).str.strip().str.lower().str.startswith("transaction")
    outstanding = pd.to_numeric(df[outstanding_col], errors="coerce").fillna(0.0)
    mask = is_transaction & (outstanding < 0)
    return df[mask].reset_index(drop=True)


def build_untagged_summary(df_untagged_ageing: pd.DataFrame, incharge_map: dict[str, str]) -> pd.DataFrame:
    """Location-wise pivot of the Untagged Ageing rows, values in Lakhs
    (÷ 1,00,000) - this is the one summary sheet the report shows in Lakhs;
    Below 1k Summary stays in actual rupees. Sorted by Accounted Outstanding
    ascending."""
    outstanding_col = _find_col(df_untagged_ageing.columns, _ACCOUNTED_OUTSTANDING_TOKENS)
    subset = df_untagged_ageing.copy()
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
    pivot[COUNT_COL] = _invoice_counts(subset, pivot)

    value_cols = ["Accounted Outstanding", *BUCKET_COLS]
    pivot[value_cols] = pivot[value_cols] / _LAKH

    pivot["Above 30 Days"] = pivot["Accounted Outstanding"] - pivot[BUCKET_COLS[0]] - pivot[BUCKET_COLS[1]]
    pivot["Account Incharges"] = pivot[LOCATION_COL].map(lambda loc: incharge_map.get(loc, ""))
    pivot = pivot.sort_values("Accounted Outstanding").reset_index(drop=True)

    return pivot[[LOCATION_COL, "Account Incharges", COUNT_COL, "Accounted Outstanding", *BUCKET_COLS, "Above 30 Days"]]


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
    # "Invoice No/ Receipt No" and "Invoice Type" contain "invoice" but are identifiers, not amounts
    if "no/" in low or "type" in low:
        return False
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
        (_STYLE_NUM_INT_ODD,  PatternFill("solid", fgColor=_ROW_ODD),  _FMT_INT),
        (_STYLE_NUM_INT_EVEN, PatternFill("solid", fgColor=_ROW_EVEN), _FMT_INT),
        (_STYLE_NUM_DEC_ODD,  PatternFill("solid", fgColor=_ROW_ODD),  _FMT_DEC),
        (_STYLE_NUM_DEC_EVEN, PatternFill("solid", fgColor=_ROW_EVEN), _FMT_DEC),
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
            is_blank = _is_blank(val)
            if not is_blank and isinstance(val, _dt.datetime):
                # a date is never a number, whatever its column is called
                c.value = val
                c.style = _STYLE_DATE_EVEN if is_even_row else _STYLE_DATE_ODD
                disp = val.strftime("%d-%b-%Y")
            elif numeric_cols[ci - 1]:
                is_days = (ci - 1) == days_os_idx
                if is_blank:
                    c.value = _DASH
                    disp = _DASH
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
                    c.value = _DASH
                    disp = _DASH
                    c.style = _STYLE_TXT_EVEN if is_even_row else _STYLE_TXT_ODD
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
                       location_col_header: str, amount_col: str, above_30_formula,
                       footnote: str | None = None) -> None:
    """Write a Location-wise pivot sheet (Below 1k Summary / Untagged
    Summary) with a live-SUBTOTAL Grand Total row.

    `footnote`, if given, is written a couple of rows below the Grand
    Total row (e.g. a "values are in Lakhs" note) - the values themselves
    are expected to already be in whatever unit the footnote describes;
    this only writes the note text.

    above_30_formula(amount_ref, bucket_refs) -> the Excel formula string
    for that sheet's own "Above 30 Days" column, built from cell refs so it
    (and the Grand Total row's own copy of it) recalculates live in Excel.
    """
    ws = wb.create_sheet(title=title)
    ws.sheet_properties.tabColor = tab_color
    ws.sheet_view.showGridLines = False

    cols = [LOCATION_COL, "Account Incharges", COUNT_COL, amount_col, *BUCKET_COLS, "Above 30 Days"]
    header_labels = {
        LOCATION_COL: location_col_header,
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
    data_cols = [LOCATION_COL, "Account Incharges", COUNT_COL, amount_col, *BUCKET_COLS]
    for ri, row_vals in enumerate(df[data_cols].itertuples(index=False, name=None), 2):
        row_fill = fill_even if ri % 2 == 0 else fill_odd
        row_cells = []
        for col_name, val in zip(data_cols, row_vals):
            c = WriteOnlyCell(ws)
            c.border = bdr
            c.fill = row_fill
            if col_name in (LOCATION_COL, "Account Incharges"):
                c.value = _DASH if _is_blank(val) else str(val)
                c.font = txt_font
                c.alignment = align_left
            else:
                c.font = num_font
                c.alignment = align_right
                is_count = col_name == COUNT_COL
                c.number_format = _FMT_INT if is_count else _FMT_DEC
                if _is_blank(val):
                    c.value = _DASH
                else:
                    c.value = int(val) if is_count else float(val)
            row_cells.append(c)

        report_amount_ref = f"{get_column_letter(amount_col_idx)}{ri}"
        bucket_refs = [f"{get_column_letter(i)}{ri}" for i in range(bucket_first_idx, bucket_last_idx + 1)]
        above_cell = WriteOnlyCell(ws, value=above_30_formula(report_amount_ref, bucket_refs))
        above_cell.font = num_font
        above_cell.alignment = align_right
        above_cell.number_format = _FMT_DEC
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

    for ci in range(3, n_cols):  # count + amount + bucket cols (excludes Above 30 Days)
        col_letter = get_column_letter(ci)
        c = WriteOnlyCell(ws)
        if n_rows:
            c.value = f"=SUBTOTAL(9,{col_letter}2:{col_letter}{n_rows + 1})"
        else:
            c.value = 0
        c.number_format = _FMT_INT if cols[ci - 1] == COUNT_COL else _FMT_DEC
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
    grand_above.number_format = _FMT_DEC
    grand_above.border = bdr
    grand_cells.append(grand_above)

    ws.append(grand_cells)

    if footnote:
        ws.append([])  # blank spacer row
        note_cell = WriteOnlyCell(ws, value=footnote)
        note_cell.font = Font(name="Segoe UI", size=9, italic=True, color="808080")
        ws.append([note_cell])

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 18
    for ci in range(3, n_cols + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 15

    ws.freeze_panes = "A2"
    if n_rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{n_rows + 1}"


def write_report(
    df_ageing: pd.DataFrame,
    df_untagged_summary: pd.DataFrame,
    df_untagged_ageing: pd.DataFrame,
    df_below_1k_summary: pd.DataFrame,
    df_below_1k_ageing: pd.DataFrame,
    output_path: str | Path,
    as_on_date: _dt.date,
    log_q=None,
    progress_cb=None,
) -> None:
    """Write the 5-sheet workbook, in the required order:
    Untagged Summary, Untagged Ageing, Below 1k Summary, Below 1k
    Ageing, Ageing.

    Built as a write-only workbook: every sheet is streamed row-by-row via
    ws.append() rather than kept as normal openpyxl Cell objects, which is
    what makes this practical at 200k+ rows (see _write_detail_sheet).
    "Ageing" is written last since it's by far the largest sheet - most of
    the write-phase progress span belongs to it."""
    wb = Workbook(write_only=True)

    # Both summaries' "Above 30 Days" is Total minus the first two bucket
    # columns (0-15 + 16-30 Days = 0-30 days), so both use the same formula.
    # Rounded so float noise never turns a true zero into a visible 0.00.
    above_30_formula = lambda amount_ref, bucket_refs: f"=ROUND({amount_ref}-{bucket_refs[0]}-{bucket_refs[1]},2)"

    if progress_cb:
        progress_cb(0.55, "Writing Untagged Summary...")
    _write_pivot_sheet(
        wb, "Untagged Summary", df_untagged_summary, "6A1B9A",
        location_col_header="Location Name", amount_col="Accounted Outstanding",
        above_30_formula=above_30_formula,
        footnote="All amounts in Lakhs (÷ 1,00,000)",
    )

    if progress_cb:
        progress_cb(0.60, "Writing Untagged Ageing...")
    _write_detail_sheet(
        wb, "Untagged Ageing", df_untagged_ageing, "37474F",
        log_q=log_q, progress_cb=progress_cb, base=0.60, span=0.03,
    )

    if progress_cb:
        progress_cb(0.65, "Writing Below 1k Summary...")
    _write_pivot_sheet(
        wb, "Below 1k Summary", df_below_1k_summary, "00897B",
        location_col_header="Location Name", amount_col="Total O/S",
        above_30_formula=above_30_formula,
    )

    if progress_cb:
        progress_cb(0.68, "Writing Below 1k Ageing...")
    _write_detail_sheet(
        wb, "Below 1k Ageing", df_below_1k_ageing, "C0CA33",
        log_q=log_q, progress_cb=progress_cb, base=0.68, span=0.05,
    )

    if progress_cb:
        progress_cb(0.73, "Writing Ageing (full detail)...")
    _write_detail_sheet(
        wb, "Ageing", df_ageing, "1F3864",
        log_q=log_q, progress_cb=progress_cb, base=0.73, span=0.25,
    )

    output_path = Path(output_path)
    wb.save(output_path)
    if log_q:
        log_q.put(("success", f"Saved -> {output_path}"))
    if progress_cb:
        progress_cb(1.0, "Report ready")
