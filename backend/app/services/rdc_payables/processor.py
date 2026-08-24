import re
import io
import math
import os
import atexit
from datetime import datetime
from calendar import monthrange
from concurrent.futures import ProcessPoolExecutor

import openpyxl
import pandas as pd
import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

from . import mapping_store

# ── Public constants ──────────────────────────────────────────────────────────

BLUE_COLOR          = "#8db3e2"
METADATA_TR_CLASSES = {"c2", "c31", "c32", "c35", "c36"}

# Indian digit grouping (thousand, then repeating 2-digit lakh/crore groups,
# e.g. 1,23,45,678) with no decimal places, used for every numeric cell in
# the workbook. Excel's custom-format engine repeats the width of the last
# comma-separated group leftward, so "#,##,##0" alone (no locale prefix
# needed) renders Indian-style grouping regardless of the system's own
# locale; negative numbers automatically get a leading "-" since only one
# (positive) format section is given.
_INDIAN_INT_FMT = "#,##,##0"

# Below this size the file is parsed on the main process — spawning workers
# costs more than it saves for small reports. Above it, parsing is split
# across processes (see _parse_html_report_parallel).
_PARALLEL_THRESHOLD_BYTES = 8 * 1024 * 1024

# Columns written as native Excel dates
_DATE_COLS = frozenset({"invoice date", "header gl date"})

# Columns written as Excel numbers (not text)
_NUM_COLS = frozenset({"vendor number", "accounted outstanding amount"})

# HOME / OFFICE / MUMBAI vendor-site codes resolved via Distribution Account
# segment 3 (looked up against the Location Code Map instead of the vendor
# mapping directly).
_DIST_DERIVED_SITES = frozenset({"HOME", "OFFICE", "MUMBAI"})

# Columns to drop from the raw ERP output
_COLS_TO_DROP = frozenset({
    "msme registration number",
    "msme classification type",
    "emp number",
    "doc no",
    "invoice currency code",
    "accounted currency invoice amount",
    # Q-X aging columns already present in the ERP report
    "due date",
    "days past due",
    "0-60 days",
    "61-90 days",
    "91-180 days",
    "181-365 days",
    "above 365",
    # Unnamed trailing column
    "",
})


# ---------------------------------------------------------------------------
# Mapping helpers (thin wrappers kept for import compatibility)
# ---------------------------------------------------------------------------

def load_all_mappings(mapping_path):
    """Return (vendor_mapping, loc_code_map, exclusions_set, invoice_overrides,
    region_incharge_map, txn_type_overrides)."""
    return mapping_store.load_all(mapping_path)


def load_mapping(mapping_path):
    """Return vendor_mapping only (backward-compat)."""
    m, _, _, _, _, _ = mapping_store.load_all(mapping_path)
    return m


def load_loc_code_map(mapping_path):
    """Return loc_code_map only (backward-compat)."""
    _, lcm, _, _, _, _ = mapping_store.load_all(mapping_path)
    return lcm


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------
#
# All patterns are precompiled at import time. The module-level `re.search`/
# `re.sub`/`re.findall` functions do a cache lookup on every call to find (or
# compile-and-cache) the underlying Pattern object; calling the bound method
# on an already-compiled Pattern skips that lookup entirely. That overhead is
# negligible for a handful of calls, but these run millions of times over a
# large ERP report export, where it adds up to real wall-clock time.

_RE_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
_RE_CSS_RULE    = re.compile(r"\.(c\d+)\s*\{([^}]*)\}")
_RE_TR          = re.compile(r"<tr[^>]*>.*?</tr>", re.DOTALL)
_RE_TR_CLASS    = re.compile(r"<tr[^>]*class=[\"']([^\"']*)[\"']")
_RE_TD_CLASS    = re.compile(r"<td[^>]*class=[\"']([^\"']*)[\"']")
_RE_TD_CELLS    = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_RE_HAS_BLANKS  = re.compile(r"\(blanks\)", re.IGNORECASE)

_RE_TAG = re.compile(r"<[^>]+>")


def _parse_blue_classes(css_text):
    """Extract CSS class names that have a blue background colour."""
    blue = set()
    for m in _RE_CSS_RULE.finditer(css_text):
        if BLUE_COLOR in m.group(2):
            blue.add(m.group(1))
    return blue


def _cell_text(html):
    """Strip tags and decode the five most common HTML entities."""
    if not html:
        return ""
    # Plain-string re.sub (literal replacement, no per-match callback) uses
    # an optimized C-level path — noticeably faster than a callback-driven
    # single pass. Entities are rare, so gate the six .replace() calls
    # behind a cheap "&" membership check instead of always running them.
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


def _is_blue_row(row_html, blue_classes):
    """Return True when the first TD carries a blue-background CSS class."""
    m = _RE_TD_CLASS.search(row_html)
    return bool(m and any(c in blue_classes for c in m.group(1).split()))


def _get_tr_class(row_html):
    m = _RE_TR_CLASS.search(row_html)
    return m.group(1).strip() if m else ""


def _classify_row(row_html, blue_classes):
    """
    Parse one <tr>...</tr> chunk and classify it.

    Returns (kind, texts) where kind is one of:
      "skip"   – metadata / "(blanks)" / entirely-blank row (texts is None)
      "stop"   – the "Unpaid Prepayment's Details" sentinel (texts is None)
      "header" – blue-background row; a column-header candidate
      "data"   – ordinary data row

    Pulled out of parse_html_report so the exact same per-row logic can run
    either inline (small/medium files) or inside a worker process (large
    files) — see _parse_html_report_parallel.
    """
    tr_cls = _get_tr_class(row_html)
    if tr_cls in METADATA_TR_CLASSES:
        return "skip", None

    cells_html = _RE_TD_CELLS.findall(row_html)
    texts      = [_cell_text(c) for c in cells_html]

    if any("Unpaid Prepayment" in t for t in texts):
        return "stop", None

    if any(_RE_HAS_BLANKS.search(t) for t in texts):
        return "skip", None

    if _is_blue_row(row_html, blue_classes):
        return "header", texts

    if not any(texts):
        return "skip", None

    return "data", texts


def _reduce_classified_rows(classified):
    """
    Apply the sequential semantics (first header wins, stop at sentinel) to
    an in-order iterable of (kind, texts) results, however they were
    produced. Returns (columns, data_rows).
    """
    columns   = None
    data_rows = []
    for kind, texts in classified:
        if kind == "skip":
            continue
        if kind == "stop":
            break
        if kind == "header":
            if columns is None:
                columns = texts
            continue
        data_rows.append(texts)
    return columns, data_rows


_RE_TR_START = re.compile(r"<tr[^>]*>")

# Lazily-created worker pool, reused for the lifetime of the process. Worker
# processes each pay the cost of importing pandas/numpy/openpyxl/xlsxwriter
# on startup (well over a second combined) — paying that once per app
# session instead of once per report is what makes the parallel path a net
# win rather than a wash.
_POOL = None


def _get_pool():
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=os.cpu_count() or 1)
        atexit.register(_POOL.shutdown, wait=False, cancel_futures=True)
    return _POOL


def _classify_chunk_text(chunk_text, blue_classes):
    """Worker-process entry point: find and classify every row in a
    contiguous slice of the document (both the row-boundary scan and the
    per-row tag/entity stripping happen inside the worker)."""
    return [_classify_row(m.group(0), blue_classes)
            for m in _RE_TR.finditer(chunk_text)]


def _find_chunk_boundaries(content, n_chunks):
    """
    Split `content` into up to n_chunks contiguous character ranges, each
    boundary aligned to a "<tr" start so no row is ever cut in half. Uses a
    cheap tag-position-only scan (no DOTALL row matching) to stay fast even
    on documents hundreds of MB long.
    """
    starts = [m.start() for m in _RE_TR_START.finditer(content)]
    if len(starts) < 2:
        return [0, len(content)]

    n_chunks = max(1, min(n_chunks, len(starts)))
    step     = len(starts) / n_chunks
    boundaries = [0]
    for i in range(1, n_chunks):
        boundaries.append(starts[int(i * step)])
    boundaries.append(len(content))
    # dedupe in case rounding produced repeats, keep it sorted/ascending
    return sorted(set(boundaries))


def _parse_html_report_parallel(content, blue_classes):
    """
    Parse the report across a pool of worker processes: each worker gets a
    contiguous slice of the raw document (not a giant pre-split list of row
    strings) and does its own row-boundary scan + classification, so both
    the expensive steps — not just the classification — run in parallel.
    Only the sliced text going in and the small (kind, texts) results coming
    back cross the process boundary.
    """
    pool = _get_pool()
    n_workers  = pool._max_workers if hasattr(pool, "_max_workers") else (os.cpu_count() or 1)
    boundaries = _find_chunk_boundaries(content, n_workers)
    chunks     = [content[boundaries[i]:boundaries[i + 1]]
                  for i in range(len(boundaries) - 1)]

    results = list(pool.map(_classify_chunk_text,
                            chunks, [blue_classes] * len(chunks)))

    # Chunks are consecutive slices of the document processed out of order
    # by the pool but returned in submission order by map(), so
    # concatenating them reproduces the exact sequential row order.
    flat = (pair for chunk_result in results for pair in chunk_result)
    return _reduce_classified_rows(flat)


def _read_report_text(file_path):
    """
    Read the report file as text. ERP exports are normally UTF-8, but some
    Oracle BI Publisher configurations emit Windows-1252 (cp1252) instead —
    a strict UTF-8 read then raises UnicodeDecodeError on any curly quote or
    accented character. Fall back rather than crashing the whole run.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="cp1252", errors="replace") as fh:
            return fh.read()


# ---------------------------------------------------------------------------
# Main HTML parser
# ---------------------------------------------------------------------------

def parse_html_report(file_path):
    """
    Parse an Oracle BI Publisher HTML-XLS report.

    Steps
    -----
    1. Skip metadata rows (CSS classes c2, c31, c32, c35, c36).
    2. Stop at the "Unpaid Prepayment's Details" sentinel.
    3. Treat blue-background rows as repeated column-headers / vendor totals;
       capture the first occurrence as the column names.
    4. Drop rows containing "(blanks)".
    5. Drop entirely blank rows.

    Large files (>= _PARALLEL_THRESHOLD_BYTES) are classified across a pool
    of worker processes to use every CPU core; anything smaller is parsed
    inline, since spawning workers would cost more than it saves. Any
    failure in the parallel path (restricted sandbox, no spawn support,
    single-core machine, etc.) falls back to the inline path so a report
    still processes correctly either way.

    Returns
    -------
    columns : list[str]
    rows    : list[list[str]]
    """
    content = _read_report_text(file_path)

    style_m      = _RE_STYLE_BLOCK.search(content)
    blue_classes = _parse_blue_classes(style_m.group(1)) if style_m else set()

    if len(content) >= _PARALLEL_THRESHOLD_BYTES and (os.cpu_count() or 1) > 1:
        try:
            return _parse_html_report_parallel(content, blue_classes)
        except Exception:
            pass  # fall through to the inline path below

    classified = (_classify_row(m.group(0), blue_classes)
                  for m in _RE_TR.finditer(content))
    return _reduce_classified_rows(classified)


# ---------------------------------------------------------------------------
# Date / amount helpers
# ---------------------------------------------------------------------------

_DATE_FMTS = ("%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y")

_MONTH_NUM = {m: i + 1 for i, m in enumerate((
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}


def _parse_date(val):
    """
    Parse a date string in one of the ERP export's formats.

    The overwhelming majority of values are "DD-MON-YY"/"DD-MON-YYYY"
    (e.g. "18-JUN-26") — datetime.strptime handles this correctly but pays
    for a general-purpose locale-aware regex match on every call. This is
    called per date cell across the whole report (often twice, for GL date
    and invoice date), so a hand-rolled fast path for the dash-separated
    format — matching strptime's own "%y" century pivot (00-68 -> 2000s,
    69-99 -> 1900s) exactly — avoids that overhead. Anything that doesn't
    fit the fast path (slash-separated dates, garbage, blanks) falls back to
    the original strptime loop unchanged.
    """
    s = val if isinstance(val, str) else str(val)
    s = s.strip()
    if not s:
        return None

    parts = s.split("-")
    if len(parts) == 3:
        day_s, mon_s, year_s = parts
        month = _MONTH_NUM.get(mon_s.upper())
        if month and day_s.isdigit() and year_s.isdigit():
            try:
                if len(year_s) == 2:
                    yy   = int(year_s)
                    year = yy + (2000 if yy <= 68 else 1900)
                elif len(year_s) == 4:
                    year = int(year_s)
                else:
                    year = None
                if year is not None:
                    return datetime(year, month, int(day_s))
            except ValueError:
                pass  # e.g. day out of range for that month — fall through

    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _parse_amount(val):
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def cutoff_for_month(year, month):
    """Last day of the given month as a datetime (public utility)."""
    last_day = monthrange(year, month)[1]
    return datetime(year, month, last_day)


# ---------------------------------------------------------------------------
# Report transformation
# ---------------------------------------------------------------------------

def process_report(columns, data_rows, cutoff_year, cutoff_month,
                   mapping, loc_code_map=None, exclusions=None,
                   invoice_overrides=None, region_incharge_map=None,
                   txn_type_overrides=None):
    """
    Apply all transformation steps defined in the spec.

    Parameters
    ----------
    columns            : list[str]
    data_rows          : list[list[str]]
    cutoff_year        : int  – GL year of the cutoff month
    cutoff_month        : int – GL month number of the cutoff month
    mapping            : dict – vendor_mapping from load_all_mappings
    loc_code_map       : dict – location code mapping (optional)
    exclusions         : set  – (vendor_num_upper, inv_num_upper) pairs to drop
    invoice_overrides  : dict – {(vendor_num_upper, inv_num_upper): {"Region":..,
                                 "Accounts Incharge":..}} explicit per-invoice
                                 overrides. An entry keyed with a blank invoice
                                 number, e.g. (vendor_num_upper, ""), applies to
                                 every invoice from that vendor.
    region_incharge_map: dict – {Region: Accounts Incharge}. When a Region has
                                 an entry here, it always wins over whatever
                                 Accounts Incharge value came from the vendor /
                                 location / invoice-override mapping, since a
                                 region always has exactly one incharge.
    txn_type_overrides : dict – {(vendor_num_upper, inv_num_upper): "Transaction
                                 Type"} pins a specific Transaction Type for that
                                 vendor/invoice, taking priority over every
                                 automatic classification rule below. An entry
                                 keyed with a blank invoice number, e.g.
                                 (vendor_num_upper, ""), applies to every
                                 invoice from that vendor.

    Only rows whose GL date falls on or before the last day of
    (cutoff_year, cutoff_month) are kept — i.e. everything from the start of
    the timeline through the selected month is included; later months are
    dropped. Rows with an unparseable/blank GL date are kept (can't be judged).

    Returns
    -------
    (pd.DataFrame, reporting_ref_date)
    """
    df = pd.DataFrame(data_rows, columns=columns)
    df.columns = [c.strip() for c in df.columns]

    # ── Include GL data only through the selected month ───────────────────────
    reporting_ref_date = None
    gl_col = next(
        (c for c in df.columns if c.strip().lower() in ("header gl date", "gl date")),
        None,
    )
    if gl_col:
        cutoff_date = cutoff_for_month(cutoff_year, cutoff_month)
        df["_gl"]   = df[gl_col].apply(_parse_date)
        within_cutoff = df["_gl"].apply(
            lambda d: d is None or d <= cutoff_date
        )
        df = df[within_cutoff].copy()
        valid_gl = df["_gl"].dropna()
        if not valid_gl.empty:
            mx       = valid_gl.max()
            last_day = monthrange(mx.year, mx.month)[1]
            reporting_ref_date = datetime(mx.year, mx.month, last_day)
        df.drop(columns=["_gl"], inplace=True)

    # ── Drop unnecessary columns ───────────────────────────────────────────────
    to_drop = [c for c in df.columns if c.strip().lower() in _COLS_TO_DROP]
    df.drop(columns=to_drop, errors="ignore", inplace=True)

    # ── Insert Region + Accounts Incharge ─────────────────────────────────────
    if "Vendor Site Code" in df.columns:
        idx      = df.columns.get_loc("Vendor Site Code")
        dist_col = next(
            (c for c in df.columns if c.strip().lower() == "distribution account"), None
        )
        regions  = []
        accounts = []
        dist_vals = df[dist_col] if dist_col else [""] * len(df)
        for vsc, dist_val in zip(df["Vendor Site Code"], dist_vals):
            vsc_norm = str(vsc).strip().upper()
            if vsc_norm in _DIST_DERIVED_SITES and loc_code_map:
                parts    = str(dist_val).split(".")
                loc_code = parts[2].strip() if len(parts) > 2 else ""
                entry    = loc_code_map.get(loc_code, {})
            else:
                entry = mapping.get(vsc_norm, {})
            regions.append(entry.get("Region", ""))
            accounts.append(entry.get("Accounts Incharge", ""))
        df.insert(idx + 1, "Region",            regions)
        df.insert(idx + 2, "Accounts Incharge", accounts)

    # ── Invoice-level overrides (Vendor Number + Invoice Number) ──────────────
    # Takes priority over the Vendor Site Code mapping above — lets specific
    # invoices be pinned to a Region / Accounts Incharge regardless of site
    # code. An override keyed with a blank invoice number, e.g.
    # (vendor_num_upper, ""), is vendor-wide: it applies to every invoice from
    # that vendor. A specific (vendor, invoice) override still wins over the
    # vendor-wide one when both exist.
    if invoice_overrides and "Region" in df.columns and "Accounts Incharge" in df.columns:
        vn_col  = next((c for c in df.columns if c.strip().lower() == "vendor number"),  None)
        inv_col = next((c for c in df.columns if c.strip().lower() == "invoice number"), None)
        if vn_col and inv_col:
            df["_ovn"]  = df[vn_col].astype(str).str.strip().str.upper()
            df["_oinv"] = df[inv_col].astype(str).str.strip().str.upper()

            vendor_wide_ov = {vn: e for (vn, inv), e in invoice_overrides.items() if inv == ""}
            exact_ov       = {(vn, inv): e for (vn, inv), e in invoice_overrides.items() if inv != ""}

            if vendor_wide_ov:
                vw_rows = [(vn, e.get("Region", ""), e.get("Accounts Incharge", ""))
                           for vn, e in vendor_wide_ov.items()]
                vw_df = pd.DataFrame(vw_rows, columns=["_ovn", "_vregion", "_vincharge"])
                df = df.merge(vw_df, on="_ovn", how="left")
                has_vw = df["_vregion"].notna()
                df.loc[has_vw, "Region"]            = df.loc[has_vw, "_vregion"]
                df.loc[has_vw, "Accounts Incharge"] = df.loc[has_vw, "_vincharge"]
                df.drop(columns=["_vregion", "_vincharge"], inplace=True)

            if exact_ov:
                ov_rows = [(vn, inv, e.get("Region", ""), e.get("Accounts Incharge", ""))
                           for (vn, inv), e in exact_ov.items()]
                ov_df = pd.DataFrame(ov_rows, columns=["_ovn", "_oinv", "_oregion", "_oincharge"])
                df = df.merge(ov_df, on=["_ovn", "_oinv"], how="left")
                has_ov = df["_oregion"].notna()
                df.loc[has_ov, "Region"]            = df.loc[has_ov, "_oregion"]
                df.loc[has_ov, "Accounts Incharge"] = df.loc[has_ov, "_oincharge"]
                df.drop(columns=["_oregion", "_oincharge"], inplace=True)

            df.drop(columns=["_ovn", "_oinv"], inplace=True)

    # ── Region → Accounts Incharge (one incharge per region) ─────────────────
    # When a region has a fixed incharge on file, that always wins over
    # whatever incharge value came from the vendor / location / invoice
    # mappings above, so a region's incharge only needs to be maintained once.
    if region_incharge_map and "Region" in df.columns and "Accounts Incharge" in df.columns:
        rim_series = df["Region"].map(region_incharge_map)
        has_rim = rim_series.notna() & (rim_series != "")
        df.loc[has_rim, "Accounts Incharge"] = rim_series[has_rim]

    # ── Keep only negative Accounted Outstanding Amount ───────────────────────
    amt_col = next(
        (c for c in df.columns if c.strip().lower() == "accounted outstanding amount"),
        None,
    )
    if amt_col:
        df["_amt"] = df[amt_col].apply(_parse_amount)
        df = df[df["_amt"].notna() & (df["_amt"] < 0)].copy()
        df.drop(columns=["_amt"], inplace=True)

    # ── Transaction Type classification (priority order) ───────────────────────
    df["Transaction Type"] = ""

    dist_col = next(
        (c for c in df.columns if c.strip().lower() == "distribution account"), None
    )
    inv_num_col  = next(
        (c for c in df.columns if c.strip().lower() == "invoice number"), None
    )
    inv_type_col = next(
        (c for c in df.columns if c.strip().lower() == "invoice type"), None
    )

    # Priority 1 – Security Deposit (GL segment "331002")
    if dist_col:
        mask = (df[dist_col].fillna("").astype(str).str.strip()
                .str.contains("331002", na=False, regex=False))
        df.loc[mask & (df["Transaction Type"] == ""), "Transaction Type"] = "Security Deposit"

    # Priority 2 – TDS
    if inv_num_col:
        mask = df[inv_num_col].astype(str).str.contains("TDS", case=False, na=False)
        df.loc[mask & (df["Transaction Type"] == ""), "Transaction Type"] = "TDS"

    # Priority 3 – IOCL (also case-insensitive: IOC, Diesel, CNG, HIRE)
    if inv_num_col:
        mask = df[inv_num_col].astype(str).str.contains(
            r"IOC|Diesel|CNG|HIRE", case=False, na=False, regex=True
        )
        df.loc[mask & (df["Transaction Type"] == ""), "Transaction Type"] = "IOCL"

    # Priority 4 – Prepayment
    if inv_type_col:
        mask = df[inv_type_col].astype(str).str.contains("Prepayment", case=False, na=False)
        df.loc[mask & (df["Transaction Type"] == ""), "Transaction Type"] = "Prepayment"

    # Priority 5 – Other
    df.loc[df["Transaction Type"] == "", "Transaction Type"] = "Other"

    # ── Transaction Type overrides (Vendor Number + Invoice Number) ──────────
    # Takes priority over every automatic rule above — the whole point is to
    # pin a category down for good when the automatic rules get it wrong or
    # can't infer it. Applied before the IOCL date-cutoff filter below, so a
    # row fixed to/from "IOCL" here is correctly subject to (or exempt from)
    # that rule. A blank-invoice entry, e.g. (vendor_num_upper, ""), is
    # vendor-wide — same convention as the invoice-overrides map above.
    if txn_type_overrides:
        vn_col = next((c for c in df.columns if c.strip().lower() == "vendor number"), None)
        if vn_col:
            df["_tvn"] = df[vn_col].astype(str).str.strip().str.upper()

            vendor_wide_tt = {vn: tt for (vn, inv), tt in txn_type_overrides.items() if inv == ""}
            exact_tt       = {(vn, inv): tt for (vn, inv), tt in txn_type_overrides.items() if inv != ""}

            if vendor_wide_tt:
                vw_df = pd.DataFrame(list(vendor_wide_tt.items()), columns=["_tvn", "_vtt"])
                df = df.merge(vw_df, on="_tvn", how="left")
                has_vw = df["_vtt"].notna()
                df.loc[has_vw, "Transaction Type"] = df.loc[has_vw, "_vtt"]
                df.drop(columns=["_vtt"], inplace=True)

            if exact_tt and inv_num_col:
                df["_tinv"] = df[inv_num_col].astype(str).str.strip().str.upper()
                ov_rows = [(vn, inv, tt) for (vn, inv), tt in exact_tt.items()]
                ov_df   = pd.DataFrame(ov_rows, columns=["_tvn", "_tinv", "_ott"])
                df = df.merge(ov_df, on=["_tvn", "_tinv"], how="left")
                has_ov = df["_ott"].notna()
                df.loc[has_ov, "Transaction Type"] = df.loc[has_ov, "_ott"]
                df.drop(columns=["_tinv", "_ott"], inplace=True)

            df.drop(columns=["_tvn"], inplace=True)

    # Move Transaction Type column immediately after Accounts Incharge
    cols = list(df.columns)
    if "Transaction Type" in cols and "Accounts Incharge" in cols:
        cols.remove("Transaction Type")
        cols.insert(cols.index("Accounts Incharge") + 1, "Transaction Type")
        df = df[cols]

    # ── IOCL invoice date cutoff (25th of reporting month) ───────────────────
    # Invoice date is parsed once and reused for both this filter and the
    # aging bucket below (previously each parsed the same column from
    # scratch), keeping the parsed Series filtered in lockstep with df.
    inv_date_col = next(
        (c for c in df.columns if c.strip().lower() == "invoice date"), None
    )
    inv_dates = df[inv_date_col].apply(_parse_date) if inv_date_col else None

    if reporting_ref_date and inv_date_col:
        cut25      = datetime(reporting_ref_date.year, reporting_ref_date.month, 25)
        beyond_25  = inv_dates.apply(lambda d: d is not None and d > cut25)
        iocl_mask  = df["Transaction Type"] == "IOCL"
        keep       = ~(iocl_mask & beyond_25)
        df         = df[keep].copy()
        inv_dates  = inv_dates[keep]

    # ── Aging bucket ──────────────────────────────────────────────────────────
    LE30 = "Less than or equal to 30 Days"
    GT30 = "More than 30 Days"
    if inv_date_col and reporting_ref_date:

        def _bucket(d):
            if d is None:
                return GT30
            return LE30 if (reporting_ref_date - d).days <= 30 else GT30

        df["Aging Bucket"] = inv_dates.apply(_bucket)
    else:
        df["Aging Bucket"] = GT30

    # ── Row exclusions ────────────────────────────────────────────────────────
    # Drop rows whose (Vendor Number, Invoice Number) pair appears in the
    # exclusions set. An entry keyed with a blank invoice number, e.g.
    # (vendor_num_upper, ""), is vendor-wide: it drops every row from that
    # vendor regardless of invoice number — same convention as the
    # invoice-overrides vendor-wide entries above. Uses vectorised merges
    # for performance on large DataFrames.
    if exclusions:
        vn_col  = next((c for c in df.columns if c.strip().lower() == "vendor number"),  None)
        inv_col = next((c for c in df.columns if c.strip().lower() == "invoice number"), None)
        if vn_col:
            df["_xvn"] = df[vn_col].astype(str).str.strip().str.upper()

            if inv_col:
                vendor_wide_excl = {vn for vn, inv in exclusions if inv == ""}
                exact_excl       = {(vn, inv) for vn, inv in exclusions if inv != ""}

                if vendor_wide_excl:
                    df = df[~df["_xvn"].isin(vendor_wide_excl)]

                if exact_excl:
                    df["_xinv"] = df[inv_col].astype(str).str.strip().str.upper()
                    excl_df = pd.DataFrame(list(exact_excl), columns=["_xvn", "_xinv"])
                    df = df.merge(excl_df, on=["_xvn", "_xinv"], how="left", indicator=True)
                    df = df[df["_merge"] == "left_only"].drop(columns=["_merge", "_xinv"])
            else:
                # No Invoice Number column in this report at all — every
                # exclusion entry can only be matched by vendor number.
                excl_vns = {vn for vn, _inv in exclusions}
                df = df[~df["_xvn"].isin(excl_vns)]

            df = df.drop(columns=["_xvn"])

    df.reset_index(drop=True, inplace=True)
    return df, reporting_ref_date


# ---------------------------------------------------------------------------
# Excel output
# ---------------------------------------------------------------------------

def to_excel_bytes(df, missing_codes=None):
    """
    Write the report DataFrame to xlsx bytes with rich formatting.

    Sheets
    ------
    1. "Main"               – pivot by Transaction Type × Aging Bucket
    2. "Summary"            – main transformed data (all entries)
    3. "Mapping Not Found"  – (optional) vendor site codes with no mapping
    """
    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {"in_memory": True})

    # ── Shared format palette ─────────────────────────────────────────────────
    hdr_fmt  = wb.add_format({
        "bold": True, "bg_color": "#B7DEE8", "font_color": "#000000",
        "border": 1, "border_color": "#000000",
    })
    cell_fmt = wb.add_format({"border": 1, "border_color": "#000000"})
    date_fmt = wb.add_format({
        "border": 1, "border_color": "#000000", "num_format": "dd-mmm-yy",
    })

    # ── Sheet writer helper ───────────────────────────────────────────────────

    def _write_sheet(ws, columns, rows_iter, col_widths,
                     date_col_indices=(), num_col_indices=(),
                     hidden_col_indices=(), subtotal_col_index=None,
                     subtotal_value=None, parsed_dates=None, parsed_amounts=None):
        """Write the sheet body with appropriate cell formats.

        Normally: header row at 0, data starting at row 1.

        When subtotal_col_index is given, the layout instead is:
          row 0 — a fully-blank row except subtotal_col_index, which holds a
                  live =SUBTOTAL(109, ...) formula over the data range (so it
                  reflects filtered/hidden rows too, and recalculates if the
                  data changes) rather than a hard-coded number.
          row 1 — header row
          row 2+ — data rows

        Returns (header_row, data_row_start) — the 0-indexed row the header
        landed on, and the 0-indexed row the data starts on — so the caller
        can set the autofilter range correctly in either layout.
        """
        rows = list(rows_iter)
        # Indian digit grouping (thousands, then 2-digit lakh/crore groups)
        # with no decimal places — "#,##,##0" rather than the US-style
        # "#,##0.00"/"#,##0" — for actual monetary amounts.
        num_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                                  "num_format": _INDIAN_INT_FMT})
        # Vendor Number is an identifier, not an amount — plain digits, no
        # thousands/lakh grouping at all.
        int_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                                  "num_format": "0"})
        sub_fmt = wb.add_format({"bold": True, "num_format": _INDIAN_INT_FMT})

        if subtotal_col_index is not None:
            header_row, data_row_start = 1, 2
            if rows:
                first_cell = xl_rowcol_to_cell(data_row_start, subtotal_col_index)
                last_cell  = xl_rowcol_to_cell(data_row_start + len(rows) - 1, subtotal_col_index)
                ws.write_formula(0, subtotal_col_index,
                                  f"=SUBTOTAL(109,{first_cell}:{last_cell})",
                                  sub_fmt, value=subtotal_value or 0.0)
            else:
                ws.write_number(0, subtotal_col_index, 0.0, sub_fmt)
        else:
            header_row, data_row_start = 0, 1

        for c, name in enumerate(columns):
            ws.write(header_row, c, name, hdr_fmt)

        parsed_dates   = parsed_dates or {}
        parsed_amounts = parsed_amounts or {}

        for pos, row in enumerate(rows):
            r = data_row_start + pos
            for c, val in enumerate(row):
                if c in date_col_indices:
                    # Reuse the value already parsed once up front (each
                    # date/amount column would otherwise be re-parsed from
                    # its original string here, on top of every earlier
                    # pass that needed the same column).
                    col_dates = parsed_dates.get(c)
                    dt = col_dates[pos] if col_dates is not None else _parse_date(val)
                    ws.write_datetime(r, c, dt, date_fmt) if dt else ws.write(r, c, "", cell_fmt)
                elif c in num_col_indices:
                    col_amounts = parsed_amounts.get(c)
                    amt = col_amounts[pos] if col_amounts is not None else _parse_amount(val)
                    if amt is not None:
                        fmt = int_fmt if columns[c].strip().lower() == "vendor number" else num_fmt
                        ws.write_number(r, c, amt, fmt)
                    else:
                        ws.write(r, c, "" if val is None else val, cell_fmt)
                else:
                    v = "" if (val is None or (isinstance(val, float) and math.isnan(val))) else val
                    ws.write(r, c, v, cell_fmt)
        for c, width in enumerate(col_widths):
            if c in hidden_col_indices:
                ws.set_column(c, c, width, None, {"hidden": True})
            else:
                ws.set_column(c, c, width)
        return header_row, data_row_start

    # ── Decide sheet order up front: "Main" (pivot) must be the first tab,
    #    "Summary" (all entries) the second — so both worksheets are created
    #    here, before either is written to. ──────────────────────────────────
    LE30 = "Less than or equal to 30 Days"
    GT30 = "More than 30 Days"
    # Base transaction types, in display order, plus any custom ones a user
    # has pinned via the Transaction Type Overrides mapping — without this,
    # a row whose type isn't one of the five base categories would silently
    # vanish from every pivot total below instead of just not having its own
    # dedicated column.
    _BASE_TX_ORDER = ["IOCL", "Prepayment", "TDS", "Security Deposit", "Other"]
    if "Transaction Type" in df.columns:
        _extra_tx = sorted(set(df["Transaction Type"].dropna().unique())
                           - set(_BASE_TX_ORDER) - {""})
    else:
        _extra_tx = []
    TX_ORDER = _BASE_TX_ORDER + _extra_tx

    cols = list(df.columns)
    date_col_indices = {ci for ci, col in enumerate(cols) if col.strip().lower() in _DATE_COLS}
    num_col_indices  = {ci for ci, col in enumerate(cols) if col.strip().lower() in _NUM_COLS}

    # Every date/amount column is parsed exactly once here and reused below
    # for the pivot table, the subtotal formula's seed value, and the cell
    # writer — each of those previously re-parsed the same string values
    # from scratch. Built with plain list comprehensions rather than
    # Series.apply(): apply() silently coerces the column to a uniform
    # dtype, turning a parse failure's `None` into NaN/NaT instead of
    # leaving it as None — which would then slip past the `is not None`
    # checks below and write garbage numbers/dates.
    _parsed_dates   = {ci: [_parse_date(v)   for v in df[cols[ci]]] for ci in date_col_indices}
    _parsed_amounts = {ci: [_parse_amount(v) for v in df[cols[ci]]] for ci in num_col_indices}

    outstanding_col_idx = next(
        (ci for ci, col in enumerate(cols)
         if col.strip().lower() == "accounted outstanding amount"), None)

    _req_cols  = {"Region", "Accounts Incharge", "Transaction Type", "Aging Bucket"}
    _has_pivot = _req_cols.issubset(df.columns) and outstanding_col_idx is not None

    ws_piv = wb.add_worksheet("Main") if _has_pivot else None

    # ── Sheet 2: Summary (all entries) ────────────────────────────────────────
    ws_main = wb.add_worksheet("Summary")

    # Explicit widths (Excel character units), deliberately kept tight so
    # every column from "Vendor Name" through "Accounted Outstanding Amount"
    # fits on screen without horizontal scrolling on a typical laptop when
    # the file is first opened (combined with the reduced zoom set below).
    # "Vendor Name" gets a fixed width too instead of the content-based
    # auto-sizing used for any other, unlisted column — an unbounded
    # longest-value width there would blow the whole row past what fits.
    _SUMMARY_COL_WIDTH_OVERRIDES = {
        "vendor name":                 20,
        "vendor number":               10,
        "invoice number":              16,
        "invoice type":                11,
        "vendor site code":            13,
        "region":                      13,
        "accounts incharge":           14,
        "transaction type":            11,
        "distribution account":        15,
        "accounted outstanding amount": 13,
    }

    col_widths = []
    for col in cols:
        cn = col.strip().lower()
        if cn in _SUMMARY_COL_WIDTH_OVERRIDES:
            col_widths.append(_SUMMARY_COL_WIDTH_OVERRIDES[cn])
        elif cn in _DATE_COLS:
            col_widths.append(10)
        elif cn in _NUM_COLS:
            col_widths.append(13)
        else:
            max_data = df[col].astype(str).map(len).max() if len(df) > 0 else 0
            col_widths.append(min(max(int(max_data), len(str(col))) + 2, 60))

    # Columns hidden (not deleted) in the Summary sheet — substring match so
    # "Liability Account" (as well as a bare "Liability") and "Accounted
    # Currency Amount" both get caught regardless of exact header wording.
    _HIDDEN_SUMMARY_COLS = ("liability", "accounted currency amount")
    hidden_col_indices = {ci for ci, col in enumerate(cols)
                          if any(h in col.strip().lower() for h in _HIDDEN_SUMMARY_COLS)}

    # Blank top row: only "Accounted Outstanding Amount" carries the subtotal
    outstanding_subtotal = None
    if outstanding_col_idx is not None and len(df) > 0:
        outstanding_subtotal = sum(
            v for v in _parsed_amounts[outstanding_col_idx] if v is not None)

    header_row, data_row_start = _write_sheet(ws_main, cols,
                 (tuple(row) for row in df.itertuples(index=False)),
                 col_widths,
                 date_col_indices=date_col_indices,
                 num_col_indices=num_col_indices,
                 hidden_col_indices=hidden_col_indices,
                 subtotal_col_index=outstanding_col_idx,
                 subtotal_value=outstanding_subtotal,
                 parsed_dates=_parsed_dates,
                 parsed_amounts=_parsed_amounts)
    ws_main.autofilter(header_row, 0, max(header_row, data_row_start + len(df) - 1), len(cols) - 1)

    # Zoomed out a bit so the tightened widths above comfortably fit every
    # column through "Accounted Outstanding Amount" on screen without
    # horizontal scrolling, even on a smaller/lower-resolution laptop.
    ws_main.set_zoom(85)

    # ── Sheet 1: Main (pivot summary) ─────────────────────────────────────────
    if _has_pivot:
        df_p       = df.copy()
        df_p["_n"] = [v if v is not None else 0.0
                      for v in _parsed_amounts[outstanding_col_idx]]

        _piv = df_p.pivot_table(
            index      =["Region", "Accounts Incharge"],
            columns    =["Transaction Type", "Aging Bucket"],
            values     ="_n",
            aggfunc    ="sum",
            fill_value =0,
        )

        piv_rows = []
        for (region, incharge), row_s in _piv.iterrows():
            d = {}
            for tx in TX_ORDER:
                for bkt in (LE30, GT30):
                    try:
                        d[(tx, bkt)] = float(row_s[(tx, bkt)])
                    except KeyError:
                        d[(tx, bkt)] = 0.0
            d["Grand Total"]   = sum(d.values())
            d[("Total", LE30)] = sum(d[(tx, LE30)] for tx in TX_ORDER)
            d[("Total", GT30)] = sum(d[(tx, GT30)] for tx in TX_ORDER)
            piv_rows.append((region, incharge, d))

        # ── Group by Region, sort incharges within a region by Grand Total ────
        _region_order  = []
        _region_groups = {}
        for region, incharge, d in piv_rows:
            if region not in _region_groups:
                _region_groups[region] = []
                _region_order.append(region)
            _region_groups[region].append((incharge, d))
        for region in _region_groups:
            _region_groups[region].sort(key=lambda x: x[1]["Grand Total"])
        _region_order.sort(
            key=lambda r: sum(d["Grand Total"] for _, d in _region_groups[r])
        )

        def _sum_group(rows, key):
            return sum(d[key] for _, d in rows)

        # Flat layout: one entry per row to write — a data row for every
        # (Region, Accounts Incharge), followed by a subtotal row once each
        # region's rows are exhausted. Skip the subtotal when a region only
        # has a single Accounts Incharge (nothing to sub-total there).
        piv_layout = []
        for region in _region_order:
            rows = _region_groups[region]
            for incharge, d in rows:
                piv_layout.append(("data", region, incharge, d))
            if len(rows) > 1:
                sub_d = {}
                for tx in TX_ORDER:
                    for bkt in (LE30, GT30):
                        sub_d[(tx, bkt)] = _sum_group(rows, (tx, bkt))
                sub_d["Grand Total"]   = _sum_group(rows, "Grand Total")
                sub_d[("Total", LE30)] = _sum_group(rows, ("Total", LE30))
                sub_d[("Total", GT30)] = _sum_group(rows, ("Total", GT30))
                # 3rd slot repurposed to hold the region's data-row count so the
                # writer below can build a SUBTOTAL() formula range from it.
                piv_layout.append(("subtotal", region, len(rows), sub_d))

        # ── Pivot formats ─────────────────────────────────────────────────────
        def _pfmt(props):
            base = {"border": 1, "border_color": "#000000",
                    "valign": "vcenter", "align": "center", "font_size": 10}
            base.update(props)
            return wb.add_format(base)

        # All pivot headers share one color, rgb(183,222,232). Row 0 (the
        # title + the merged group headers) is 12pt; every other row
        # (including row 1's individual column headers) is 10pt.
        _HDR_BG  = "#B7DEE8"
        hdr_row0 = _pfmt({"bold": True, "bg_color": _HDR_BG,
                           "font_color": "#000000", "text_wrap": True,
                           "font_size": 12})
        hdr_row1 = _pfmt({"bold": True, "bg_color": _HDR_BG,
                           "font_color": "#000000", "text_wrap": True})

        lbl_fmt_p = wb.add_format({"border": 1, "border_color": "#000000",
                                    "valign": "vcenter", "align": "center",
                                    "font_size": 10})
        # num_fmt_p, gt_fmt_p, tot_fmt_p all render as centered integers
        # with no background — using one shared format for all data-row cells
        num_fmt_p = wb.add_format({"border": 1, "border_color": "#000000",
                                    "num_format": _INDIAN_INT_FMT, "valign": "vcenter",
                                    "align": "center", "font_size": 10})
        # Shared dash format for zero values (no background = white)
        dash_fmt_p = wb.add_format({"border": 1, "border_color": "#000000",
                                     "align": "center", "valign": "vcenter",
                                     "font_color": "#999999", "font_size": 10})
        # Bold variants used for the "Grand Total" column and the "Total"
        # group columns (LE30/GT30) on ordinary data rows — every other row
        # type (headers, region subtotals, grand-total row) is already bold.
        num_fmt_p_bold = wb.add_format({"border": 1, "border_color": "#000000",
                                         "num_format": _INDIAN_INT_FMT, "valign": "vcenter",
                                         "align": "center", "bold": True, "font_size": 10})
        dash_fmt_p_bold = wb.add_format({"border": 1, "border_color": "#000000",
                                          "align": "center", "valign": "vcenter",
                                          "font_color": "#999999", "bold": True,
                                          "font_size": 10})

        # Region-subtotal row formats (light grey, bold)
        sub_lbl_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                                      "bold": True, "italic": True,
                                      "bg_color": "#E7E6E6", "valign": "vcenter",
                                      "align": "center", "font_size": 10})
        sub_num_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                                      "bold": True, "bg_color": "#E7E6E6",
                                      "num_format": _INDIAN_INT_FMT, "valign": "vcenter",
                                      "align": "center", "font_size": 10})
        sub_dsh_fmt = wb.add_format({"border": 1, "border_color": "#000000",
                                      "bold": True, "bg_color": "#E7E6E6",
                                      "align": "center", "valign": "vcenter",
                                      "font_color": "#999999", "font_size": 10})

        def _sub_write(row_i, col_i, value, first_row, last_row):
            if value == 0.0:
                ws_piv.write(row_i, col_i, "-", sub_dsh_fmt)
            else:
                cell_range = (f"{xl_rowcol_to_cell(first_row, col_i)}:"
                               f"{xl_rowcol_to_cell(last_row, col_i)}")
                ws_piv.write_formula(row_i, col_i, f"=SUBTOTAL(9,{cell_range})",
                                      sub_num_fmt, value)

        def _piv_write(ws_p, row_i, col_i, value, fmt_nonzero=num_fmt_p, fmt_dash=dash_fmt_p):
            """Write a numeric pivot cell; replace exactly-zero with '-'."""
            if value == 0.0:
                ws_p.write(row_i, col_i, "-", fmt_dash)
            else:
                ws_p.write_number(row_i, col_i, value, fmt_nonzero)

        # ── Worksheet layout ──────────────────────────────────────────────────
        last_col = 3 + len(TX_ORDER) * 2 + 2 - 1   # = 14

        ws_piv.set_row(0, 24)   # group-label / title row
        ws_piv.set_row(1, 52)   # flat header row (carries the autofilter)

        # Row 0: "Locationwise Summary" title + TX group merges
        ws_piv.merge_range(0, 0, 0, 2, "Locationwise Summary", hdr_row0)

        # Column widths (in Excel character units) — matched to the desired
        # layout: Region, Accounts Incharge, Grand Total, then a <=30/>30 pair
        # per transaction type, then a <=30/>30 pair for Total. Hand-tuned
        # for the 5 base transaction types; any custom types pinned via the
        # Transaction Type Overrides mapping get a plain default pair
        # inserted before the trailing Total columns instead of falling
        # through to Excel's generic default width.
        _PIVOT_COL_WIDTHS = [18, 14, 11, 10, 9, 10, 11, 9, 10, 13, 10, 10, 11, 11, 11]
        if _extra_tx:
            _PIVOT_COL_WIDTHS = (_PIVOT_COL_WIDTHS[:-2]
                                 + [10, 10] * len(_extra_tx)
                                 + _PIVOT_COL_WIDTHS[-2:])
        for c, wdt in enumerate(_PIVOT_COL_WIDTHS):
            ws_piv.set_column(c, c, wdt)

        c = 3
        for tx in TX_ORDER:
            ws_piv.merge_range(0, c, 0, c + 1, tx, hdr_row0)
            c += 2
        ws_piv.merge_range(0, c, 0, c + 1, "Total", hdr_row0)

        # Row 1: individual header cells → normal autofilter on every column
        ws_piv.write(1, 0, "Region",            hdr_row1)
        ws_piv.write(1, 1, "Accounts Incharge", hdr_row1)
        ws_piv.write(1, 2, "Grand Total",       hdr_row1)
        c = 3
        for tx in TX_ORDER:
            ws_piv.write(1, c,     LE30, hdr_row1)
            ws_piv.write(1, c + 1, GT30, hdr_row1)
            c += 2
        ws_piv.write(1, c,     LE30, hdr_row1)
        ws_piv.write(1, c + 1, GT30, hdr_row1)

        gr = len(piv_layout) + 2   # 0-indexed row index for GRAND TOTAL
        ws_piv.autofilter(1, 0, gr - 1, last_col)

        # Data rows + per-region subtotal rows
        for ri, (kind, region, incharge, d) in enumerate(piv_layout, start=2):
            if kind == "subtotal":
                row_count = incharge  # 3rd slot holds the region's data-row count
                first_row, last_row = ri - row_count, ri - 1
                ws_piv.merge_range(ri, 0, ri, 1, f"{region} — Subtotal", sub_lbl_fmt)
                _sub_write(ri, 2, d["Grand Total"], first_row, last_row)
                c = 3
                for tx in TX_ORDER:
                    _sub_write(ri, c,     d[(tx, LE30)], first_row, last_row)
                    _sub_write(ri, c + 1, d[(tx, GT30)], first_row, last_row)
                    c += 2
                _sub_write(ri, c,     d[("Total", LE30)], first_row, last_row)
                _sub_write(ri, c + 1, d[("Total", GT30)], first_row, last_row)
                continue
            ws_piv.write(ri, 0, region,   lbl_fmt_p)
            ws_piv.write(ri, 1, incharge, lbl_fmt_p)
            _piv_write(ws_piv, ri, 2, d["Grand Total"], num_fmt_p_bold, dash_fmt_p_bold)
            c = 3
            for tx in TX_ORDER:
                _piv_write(ws_piv, ri, c,     d[(tx, LE30)])
                _piv_write(ws_piv, ri, c + 1, d[(tx, GT30)])
                c += 2
            _piv_write(ws_piv, ri, c,     d[("Total", LE30)], num_fmt_p_bold, dash_fmt_p_bold)
            _piv_write(ws_piv, ri, c + 1, d[("Total", GT30)], num_fmt_p_bold, dash_fmt_p_bold)

        # ── Grand Total row — same light-blue theme as the headers ────────────
        def _gfmt(props):
            base = {"bold": True, "border": 1, "border_color": "#000000",
                    "valign": "vcenter", "bg_color": _HDR_BG,
                    "font_color": "#000000", "font_size": 10}
            base.update(props)
            return wb.add_format(base)

        g_lbl = _gfmt({"align": "center"})
        g_num = _gfmt({"num_format": _INDIAN_INT_FMT, "align": "center"})
        g_dsh = _gfmt({"align": "center"})

        def _gtot_write(col_i, value, num_fmt, dash_fmt):
            if value == 0.0:
                ws_piv.write(gr, col_i, "-", dash_fmt)
            else:
                cell_range = (f"{xl_rowcol_to_cell(2, col_i)}:"
                               f"{xl_rowcol_to_cell(gr - 1, col_i)}")
                ws_piv.write_formula(gr, col_i, f"=SUBTOTAL(9,{cell_range})",
                                      num_fmt, value)

        ws_piv.merge_range(gr, 0, gr, 1, "GRAND TOTAL", g_lbl)
        _gtot_write(2, sum(d["Grand Total"] for _, _, d in piv_rows), g_num, g_dsh)
        c = 3
        for tx in TX_ORDER:
            _gtot_write(c,     sum(d[(tx, LE30)] for _, _, d in piv_rows), g_num, g_dsh)
            _gtot_write(c + 1, sum(d[(tx, GT30)] for _, _, d in piv_rows), g_num, g_dsh)
            c += 2
        _gtot_write(c,     sum(d[("Total", LE30)] for _, _, d in piv_rows), g_num, g_dsh)
        _gtot_write(c + 1, sum(d[("Total", GT30)] for _, _, d in piv_rows), g_num, g_dsh)

    # ── Sheet 3: Mapping Not Found ────────────────────────────────────────────
    if missing_codes:
        ws_miss = wb.add_worksheet("Mapping Not Found")
        ws_miss.write(0, 0, "Vendor Site Code", hdr_fmt)
        ws_miss.set_column(0, 0, 35)
        for r, code in enumerate(sorted(missing_codes), start=1):
            ws_miss.write(r, 0, str(code), cell_fmt)

    wb.close()
    buf.seek(0)
    return buf.read()