"""Report parsing/transformation/Excel-writing logic for the Trial Balance
tool — ported verbatim (business rules unchanged) from the desktop app's
processor.py at
`umesh-sir-trial-balance-location-wise-report-automation`. The only change
from the original is that mapping data is now loaded from the shared MySQL
database (via mapping_store.load_all(db)) instead of an xlsx file path.
"""

import re
import io

import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

from . import mapping_store
from app.services.report_progress import row_progress_reporter

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# The report always starts with exactly 9 HTML rows of Oracle BI Publisher
# metadata (company/ledger/period banner text) — the 9th of those IS the
# column-header row itself. Since the input format is fixed, the app just
# hardcodes the column names below rather than reading them off that row.
_METADATA_ROWS = 9

# Internal (unique) column names used while the report is a DataFrame.
COLUMNS = ["Account Code", "Description", "Account Combination",
           "Beginning Balance", "Period Activity", "Ending Balance"]

# Display header actually written to the output workbook — deliberately
# repeats "Account" twice, matching the source report's own header row
# ("Account" = natural GL account, "Account" = full accounting flexfield).
DISPLAY_COLUMNS = ["Account", "Description", "Account",
                    "Beginning Balance", "Period Activity", "Ending Balance",
                    "Location Code", "Location Name", "Region",
                    "Accounts Incharge", "Head Office Assigned Person"]

_INDIAN_INT_FMT = "#,##,##0"
_INDIAN_AMT_FMT = "#,##,##0.00"


# ---------------------------------------------------------------------------
# Mapping helpers (thin wrapper kept for import compatibility with the
# original's function name; delegates to the DB-backed mapping_store)
# ---------------------------------------------------------------------------

def load_all_mappings(db):
    """Return (loc_name_map, region_incharge_map, name_region_map, account_ho_map)."""
    return mapping_store.load_all(db)


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

_RE_TR = re.compile(r"<tr[^>]*>.*?</tr>", re.DOTALL)
_RE_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL)
_RE_TAG = re.compile(r"<[^>]+>")


def _cell_text(html):
    if not html:
        return ""
    text = _RE_TAG.sub("", html)
    if "&" in text:
        text = (text
                .replace("&nbsp;", " ")
                .replace("&amp;",  "&")
                .replace("&lt;",   "<")
                .replace("&gt;",   ">")
                .replace("&#39;",  "'")
                .replace("&quot;", '"')
                )
    return text.strip()


def _read_report_text(file_path):
    """
    Read the report file as text. Oracle BI Publisher exports are normally
    UTF-8, but some configurations emit Windows-1252 instead — a strict
    UTF-8 read then raises UnicodeDecodeError on any curly quote or accented
    character. Fall back rather than crashing the whole run.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="cp1252", errors="replace") as fh:
            return fh.read()


def parse_html_report(file_path):
    """
    Parse the fixed-format Detail Trial Balance HTML-XLS report.

    Skips the first 9 rows (report banner + the report's own column-header
    row), then keeps every row that looks like a real data row: exactly 6
    cells, with the 3rd cell (the accounting flexfield, e.g.
    "001.000.3023.000.101001.001.00001") containing at least two dots. This
    also naturally excludes the trailing "Total" footer row (3rd cell is
    literally "Total", no dots) and any blank trailing rows.

    Returns
    -------
    columns   : list[str]  (== COLUMNS)
    data_rows : list[list[str]]
    """
    content = _read_report_text(file_path)

    trs = _RE_TR.findall(content)
    data_rows = []
    for tr in trs[_METADATA_ROWS:]:
        cells = _RE_TD.findall(tr)
        if len(cells) != 6:
            continue
        texts = [_cell_text(c) for c in cells]
        if texts[2].count(".") < 2:
            continue
        data_rows.append(texts)

    return COLUMNS, data_rows


# ---------------------------------------------------------------------------
# Location code extraction
# ---------------------------------------------------------------------------

def extract_location_code(account_combination):
    """
    Pull the 3rd dot-separated segment out of an accounting flexfield
    string (e.g. "001.000.3023.000.101001.001.00001" -> "3023"), normalised
    to an int-stripped string so "0000" -> "0" — matching how Location Code
    values are represented everywhere else (mapping tables, reference
    report). Returns "" if the string doesn't have enough segments.
    """
    parts = str(account_combination).split(".")
    if len(parts) < 3:
        return ""
    seg = parts[2].strip()
    if seg.isdigit():
        return str(int(seg))
    return seg


def _parse_amount(val):
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def distinct_account_descriptions(columns, data_rows):
    """
    Every distinct (Account Code, Description) pair found in the parsed
    report, sorted by Account Code. A natural account is 1:1 with its
    description in this report, so this doubles as the full list of GL
    accounts present — used to populate the Pivot column picker (the report
    can easily have 400+ distinct accounts, far too many to always show as
    pivot columns).
    """
    code_idx, desc_idx = columns.index("Account Code"), columns.index("Description")
    pairs = {(row[code_idx], row[desc_idx]) for row in data_rows}
    return sorted(pairs, key=lambda p: p[0])


# ---------------------------------------------------------------------------
# Report transformation
# ---------------------------------------------------------------------------

def process_report(columns, data_rows, loc_name_map, region_incharge_map,
                    name_region_map, account_ho_map):
    """
    Build the output DataFrame and cascade every row through the mapping
    tables:

        Account Combination -> Location Code
                             -> Location Name   (loc_name_map)
                             -> Region           (name_region_map)
                             -> Accounts Incharge (region_incharge_map)

        Account Code -> Head Office Assigned Person (account_ho_map)

    Head Office Assigned Person is looked up directly by the natural GL
    Account Code — a separate, independent lookup, not routed through
    Location/Region at all.

    Returns
    -------
    (pd.DataFrame, missing_codes, missing_account_ho) where missing_codes is
    the set of distinct Location Codes that end up with no Region — either
    the code itself has no Location Name mapping, or its Location Name has
    no Region mapping. Either way it's the same fix from the user's point of
    view: resolve it via POST /mappings/fix or the Manage Mappings tables.
    missing_account_ho is the set of distinct Account Codes with no entry in
    account_ho_map — previously left as a silent blank in the output column
    with no way for the user to notice or fix it.
    """
    df = pd.DataFrame(data_rows, columns=columns)

    loc_codes = df["Account Combination"].apply(extract_location_code)

    missing_codes = set()
    loc_names = []
    regions   = []
    incharges = []
    for code in loc_codes:
        name = loc_name_map.get(code, "")
        region   = name_region_map.get(name, "") if name else ""
        if code and not region:
            missing_codes.add(code)
        incharge = region_incharge_map.get(region, "") if region else ""
        loc_names.append(name)
        regions.append(region)
        incharges.append(incharge)

    df["Location Code"]     = loc_codes
    df["Location Name"]     = loc_names
    df["Region"]             = regions
    df["Accounts Incharge"] = incharges
    df["Head Office Assigned Person"] = df["Account Code"].map(account_ho_map).fillna("")
    missing_account_ho = {
        code for code in df["Account Code"].unique() if code and code not in account_ho_map
    }

    df.reset_index(drop=True, inplace=True)
    return df, missing_codes, missing_account_ho


# ---------------------------------------------------------------------------
# Excel output
# ---------------------------------------------------------------------------

def _build_pivot_sheet(wb, df, pivot_accounts, hdr_fmt):
    """
    "Pivot Summary" sheet: Region (+ its Accounts Incharge) down the rows,
    one column per user-selected (Account Code, Description) pair across
    the top, values = Sum of Ending Balance — the same shape as the
    "Sum of Ending Balance" pivot in the reference RDC TB Details workbook,
    just flattened to plain formatted cells (xlsxwriter can't create a live
    Excel PivotTable object) with a Grand Total column and a GRAND TOTAL row.

    Only rows with a resolved Region are included — unmapped rows have
    nowhere sensible to land in this summary.
    """
    ws = wb.add_worksheet("Pivot Summary")

    code_hdr_fmt = wb.add_format({
        "bold": True, "bg_color": "#B7DEE8", "font_color": "#000000",
        "border": 1, "border_color": "#000000", "align": "center",
    })
    desc_hdr_fmt = wb.add_format({
        "bold": True, "bg_color": "#B7DEE8", "font_color": "#000000",
        "border": 1, "border_color": "#000000", "align": "center",
        "text_wrap": True,
    })
    lbl_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                              "valign": "vcenter"})
    num_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                              "num_format": _INDIAN_AMT_FMT})
    dash_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                               "align": "center", "font_color": "#999999"})
    gt_lbl_fmt = wb.add_format({"bold": True, "border": 1, "border_color": "#000000",
                                 "bg_color": "#E7E6E6", "valign": "vcenter"})
    gt_num_fmt = wb.add_format({"bold": True, "border": 1, "border_color": "#000000",
                                 "bg_color": "#E7E6E6", "num_format": _INDIAN_AMT_FMT})
    gt_dash_fmt = wb.add_format({"bold": True, "border": 1, "border_color": "#000000",
                                  "bg_color": "#E7E6E6", "align": "center",
                                  "font_color": "#999999"})

    # Row 0 is a plain coloured header bar (Account Codes + "Grand Total");
    # A1/B1 are just coloured, no text. Row 1 carries the actual per-column
    # labels — Description under each account, plus "Region" / "Accounts
    # Incharge" here instead of row 0 — because row 1 is also the row the
    # autofilter below attaches to. Putting the A/B labels one row up from
    # the filter's header row left those two filter dropdowns sitting on
    # blank cells; this keeps every filtered column's label on the same row
    # as its dropdown.
    codes = [c for c, _ in pivot_accounts]
    ws.write(0, 0, "", code_hdr_fmt)
    ws.write(0, 1, "", code_hdr_fmt)
    for i, (code, _desc) in enumerate(pivot_accounts):
        col = 2 + i
        if str(code).isdigit():
            ws.write_number(0, col, int(code), code_hdr_fmt)
        else:
            ws.write(0, col, code, code_hdr_fmt)
    gt_col = 2 + len(pivot_accounts)
    ws.write(0, gt_col, "Grand Total", code_hdr_fmt)

    ws.write(1, 0, "Region", desc_hdr_fmt)
    ws.write(1, 1, "Accounts Incharge", desc_hdr_fmt)
    for i, (_code, desc) in enumerate(pivot_accounts):
        ws.write(1, 2 + i, desc, desc_hdr_fmt)
    ws.write(1, gt_col, "", desc_hdr_fmt)

    mapped = df[df["Region"] != ""].copy()
    mapped["_ending"] = mapped["Ending Balance"].apply(_parse_amount).fillna(0.0)

    regions = sorted(mapped["Region"].unique(), key=str.lower)
    # Column totals, computed in Python purely to serve as the *cached*
    # value alongside each =SUBTOTAL formula below — Excel recalculates the
    # formula itself on open, but tools that only read cached values
    # (openpyxl with data_only=True, our own verification scripts, preview
    # panes) still see the right number without needing Excel to run first.
    col_totals = [0.0] * (len(pivot_accounts) + 1)   # + 1 for Grand Total col

    data_row_start = 2
    r = data_row_start
    for region in regions:
        region_rows = mapped[mapped["Region"] == region]
        incharge = next(iter(region_rows["Accounts Incharge"]), "")
        ws.write(r, 0, region, lbl_fmt)
        ws.write(r, 1, incharge, lbl_fmt)
        row_total = 0.0
        for i, code in enumerate(codes):
            val = float(region_rows.loc[region_rows["Account Code"] == code, "_ending"].sum())
            row_total += val
            col_totals[i] += val
            (ws.write(r, 2 + i, "-", dash_fmt) if val == 0.0
             else ws.write_number(r, 2 + i, val, num_fmt))
        # Grand Total column is a per-row =SUM formula over that row's
        # account columns rather than a plain number, so it stays correct
        # even if a cell is edited by hand afterwards.
        col_totals[-1] += row_total
        row_range = f"{xl_rowcol_to_cell(r, 2)}:{xl_rowcol_to_cell(r, gt_col - 1)}"
        (ws.write(r, gt_col, "-", dash_fmt) if row_total == 0.0
         else ws.write_formula(r, gt_col, f"=SUM({row_range})", num_fmt, row_total))
        r += 1
    data_row_end = r - 1
    has_rows = data_row_end >= data_row_start

    # Column headers span two rows (Account Code, then Description) — the
    # autofilter attaches to the second (row 1), same as the reference
    # workbook's own pivot layout.
    if has_rows:
        ws.autofilter(1, 0, data_row_end, gt_col)

    # GRAND TOTAL row: every cell is a live =SUBTOTAL(9, ...) formula over
    # the region rows above, so it recalculates from what's actually visible
    # in the sheet — including if the user filters regions out via the
    # autofilter — instead of a number baked in once at generation time.
    ws.write(r, 0, "GRAND TOTAL", gt_lbl_fmt)
    ws.write(r, 1, "", gt_lbl_fmt)
    for i, total in enumerate(col_totals):
        col = 2 + i
        if not has_rows or total == 0.0:
            ws.write(r, col, "-", gt_dash_fmt)
            continue
        col_range = f"{xl_rowcol_to_cell(data_row_start, col)}:{xl_rowcol_to_cell(data_row_end, col)}"
        ws.write_formula(r, col, f"=SUBTOTAL(9,{col_range})", gt_num_fmt, total)

    ws.set_column(0, 0, 20)
    ws.set_column(1, 1, 20)
    ws.set_column(2, gt_col, 16)
    ws.freeze_panes(2, 2)
    ws.set_zoom(90)


def to_excel_bytes(df, pivot_accounts=None, progress_cb=None):
    """
    Write the report DataFrame to xlsx bytes with formatting.

    Sheets
    ------
    1. "Trial Balance"      – every row, with the 5 derived columns appended
    2. "Pivot Summary"      – (optional) Region-wise Sum of Ending Balance
                               for the user-selected Account/Description
                               columns; omitted if pivot_accounts is empty

    Unmapped Location Codes are surfaced via the job result's missing_codes
    list (for the frontend's fix-and-regenerate flow) rather than as a sheet
    in the output workbook — the output file is meant to be handed off
    as-is, not carry a working-notes tab.
    """
    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"in_memory": True})

    hdr_fmt  = wb.add_format({
        "bold": True, "bg_color": "#B7DEE8", "font_color": "#000000",
        "border": 1, "border_color": "#000000",
    })
    cell_fmt = wb.add_format({"border": 1, "border_color": "#000000"})
    int_fmt  = wb.add_format({"border": 1, "border_color": "#000000",
                               "num_format": _INDIAN_INT_FMT})
    amt_fmt  = wb.add_format({"border": 1, "border_color": "#000000",
                               "num_format": _INDIAN_AMT_FMT})

    ws = wb.add_worksheet("Trial Balance")

    for c, name in enumerate(DISPLAY_COLUMNS):
        ws.write(0, c, name, hdr_fmt)

    _AMOUNT_COLS = {3, 4, 5}   # Beginning Balance, Period Activity, Ending Balance
    _INT_COLS    = {0, 6}      # Account (natural), Location Code

    report_row = row_progress_reporter(progress_cb, base=0.7, span=0.25, total_rows=len(df))
    for r, row in enumerate(df.itertuples(index=False), start=1):
        for c, val in enumerate(row):
            if c in _AMOUNT_COLS:
                amt = _parse_amount(val)
                ws.write_number(r, c, amt, amt_fmt) if amt is not None else ws.write(r, c, "", cell_fmt)
            elif c in _INT_COLS and str(val).strip().lstrip("-").isdigit():
                ws.write_number(r, c, int(val), int_fmt)
            else:
                ws.write(r, c, "" if val is None else val, cell_fmt)
        report_row(r)

    col_widths = [12, 42, 34, 18, 16, 18, 12, 22, 18, 18, 26]
    for c, width in enumerate(col_widths):
        ws.set_column(c, c, width)

    ws.autofilter(0, 0, max(0, len(df)), len(DISPLAY_COLUMNS) - 1)
    ws.freeze_panes(1, 0)
    ws.set_zoom(90)

    if pivot_accounts:
        _build_pivot_sheet(wb, df, pivot_accounts, hdr_fmt)

    wb.close()
    buf.seek(0)
    return buf.read()
