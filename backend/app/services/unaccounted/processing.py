"""Data-processing functions for both report types."""

import os

import pandas as pd

from app.jobs import JobUserError
from app.regional import format_indian_number, today_ist

import re
from difflib import SequenceMatcher

from .constants import (
    DATA_ANCHOR_COL, MRN_ANCHOR_COL, MRN_SITE_COL,
    COLS_TO_DROP, COL_MOVE_TO_END,
    PO_ANCHOR_COL, PO_APPROVED_DATE, PO_SITE_COL, PO_ORG_COL, PO_HDR_COL,
)
from .site_mapping import SITE_MAPPING
from .mappings import _load_custom_mappings, _upsert_creator_mapping, _load_location_incharge


def _read_export_tables(path: str) -> list:
    """Read an ERP export file into pandas.read_html's list-of-tables shape.

    The ERP always exports these reports as HTML tables saved with an .xls
    extension. If a file is opened in Excel and re-saved (or downloaded via a
    different "Export to Excel" button), it becomes a genuine binary .xlsx
    (zip) file with a completely different, unsupported layout - read_html
    can't parse it at all, and pandas' own error ("No tables found matching
    regex '.+'") gives the user no clue why. Detect that case up front and
    fail with an actionable message instead.
    """
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head[:2] == b"PK":
        raise JobUserError(
            f"'{os.path.basename(path)}' is a real Excel file (.xlsx), not the "
            "original ERP export. Please re-download this report directly from "
            "the ERP (do not open and re-save it in Excel first) and upload that "
            "file instead."
        )
    try:
        return pd.read_html(path, flavor="lxml")
    except ValueError as exc:
        raise JobUserError(
            f"'{os.path.basename(path)}' doesn't look like a valid ERP export - "
            "no data table could be found in it. Please re-download this report "
            "directly from the ERP and try again."
        ) from exc


def _resolve_incharge(loc: str, stored_inc: str, loc_incharge_map: dict) -> str:
    """Accounts Incharge for a resolved Location: the one-time Location ->
    Incharge map wins when it has an entry for this location; otherwise fall
    back to whatever incharge was already stored alongside the site/creator
    mapping (keeps old data working before a location is added to the map)."""
    return loc_incharge_map.get(loc, stored_inc) or stored_inc


def find_data_table(tables: list) -> "pd.DataFrame | None":
    """Return the table whose second row contains DATA_ANCHOR_COL."""
    for df in tables:
        if df.shape[0] >= 2:
            row1_vals = [str(v).strip() for v in df.iloc[1].values]
            if DATA_ANCHOR_COL in row1_vals:
                return df
    return None


def _extract_period_name(tables: list) -> "str | None":
    """Extract the Period Name from the XLS header section (Excel cell B12).

    The Unaccounted Transactions XLS contains a header block whose row 12
    looks like:   A12: 'Period Name:'   B12: 'Jun-26'

    Sometimes pandas merges multiple header cells into one long string such as
    "Ledger Reporting Context: RDC ... Period Name: Jun-26".
    We use a regex to extract specifically the token that follows "Period Name:"
    so we always get just "Jun-26" regardless of surrounding context.

    Returns None when not found so the caller can decide on a fallback.
    """
    import re
    _PERIOD_RE = re.compile(r'Period Name\s*:\s*(\S+)', re.IGNORECASE)

    for table in tables:
        for row_idx in range(min(25, len(table))):
            for col_idx, raw_val in enumerate(table.iloc[row_idx].values):
                val_str = str(raw_val).strip()
                if "Period Name" in val_str:
                    # Regex: grab the token immediately after "Period Name:"
                    m = _PERIOD_RE.search(val_str)
                    if m:
                        period = m.group(1).strip()
                        if period and period.lower() not in ("nan", ""):
                            return period
                    # Fallback: period value is in the adjacent column
                    if col_idx + 1 < len(table.columns):
                        next_val = str(table.iloc[row_idx, col_idx + 1]).strip()
                        if next_val and next_val.lower() not in ("nan", ""):
                            return next_val
    return None


def merge_xls_files(paths: list, log_q) -> "pd.DataFrame":
    """Merge multiple XLS files that share the same structure.

    - Headers are taken from the **first** file.
    - All subsequent files contribute only data rows (their header rows are
      skipped so they don't end up as data).
    - The result is a single concatenated raw DataFrame, identical to what
      you would get from reading the first file's raw table but with the
      extra rows from the other files appended.

    Raises ValueError if any file does not contain the expected data table.
    """
    merged_parts = []
    for idx, path in enumerate(paths):
        log_q.put(("info", f"Reading file {idx + 1}/{len(paths)}: {os.path.basename(path)} …"))
        tables = _read_export_tables(path)
        raw = find_data_table(tables)
        if raw is None:
            raise JobUserError(
                f"Could not locate data table in {os.path.basename(path)}"
            )
        if idx == 0:
            merged_parts.append(raw)
        else:
            data_only = raw.iloc[2:].copy()
            merged_parts.append(data_only)
        log_q.put(("ok", f"  → {format_indian_number(max(0, len(raw) - 2))} data rows from {os.path.basename(path)}"))

    if not merged_parts:
        raise JobUserError("No input files provided.")

    combined = pd.concat(merged_parts, ignore_index=True)
    log_q.put(("info", f"Merged {format_indian_number(len(paths))} file(s) → {format_indian_number(max(0, len(combined) - 2))} total data rows"))
    return combined


def process_report(input_path: str, log_q) -> tuple:
    """Read, clean, and return (df, total_rows, input_cols, matched)."""
    log_q.put(("info", "Reading file …"))
    tables = _read_export_tables(input_path)

    raw = find_data_table(tables)
    if raw is None:
        raise JobUserError(
            f"Could not locate data table in {os.path.basename(input_path)}"
        )

    # ── Extract Period Name from B12 (e.g. "Jun-26") ──────────────────────────
    period = _extract_period_name(tables) or today_ist().strftime("%b-%y")
    log_q.put(("info", f"Period detected from file header: {period}"))

    # Row 0 = merged section title; row 1 = real column headers
    headers = [str(v).strip() for v in raw.iloc[1].values]
    df      = raw.iloc[2:].copy()
    df.columns = headers
    df      = df.reset_index(drop=True)

    # Tag every row with its source period for the pivot
    df["__month__"] = period

    total_rows = len(df)
    input_cols = len([c for c in df.columns if c != "__month__"])
    log_q.put(("info", f"Rows: {format_indian_number(total_rows)}  ·  Columns before: {format_indian_number(input_cols)}"))

    # Drop unwanted columns (ignore if already missing)
    existing_drops = [c for c in COLS_TO_DROP if c in df.columns]
    df = df.drop(columns=existing_drops)
    log_q.put(("ok", f"Columns deleted: {existing_drops}"))

    # Move Supplier Site to the end
    if COL_MOVE_TO_END in df.columns:
        cols = [c for c in df.columns if c != COL_MOVE_TO_END]
        cols.append(COL_MOVE_TO_END)
        df   = df[cols]
        log_q.put(("ok", f"'{COL_MOVE_TO_END}' moved to last column"))

    # Load custom overrides + persistent creator map from mappings.json
    site_overrides, creator_map = _load_custom_mappings()
    loc_incharge_map = _load_location_incharge()
    # Effective site mapping: hardcoded base (1131 entries) + JSON overrides
    eff_site = {**SITE_MAPPING, **site_overrides}

    df["Location"]          = df[COL_MOVE_TO_END].apply(
        lambda s: eff_site.get(str(s).strip(), ("", ""))[0])
    df["Accounts Incharge"] = df[COL_MOVE_TO_END].apply(
        lambda s: _resolve_incharge(*eff_site.get(str(s).strip(), ("", "")), loc_incharge_map))

    if site_overrides:
        log_q.put(("info", f"Applied {len(site_overrides)} custom site override(s)"))

    # For OFFICE / HOME / TDS TAX Authori: first check persistent creator_map,
    # then fall back to a dynamic lookup from this file's already-resolved rows.
    SPECIAL_SITES  = {"OFFICE", "HOME", "TDS TAX Authori"}
    special_no_loc = (df["Supplier Site"].astype(str).str.strip().isin(SPECIAL_SITES)
                      & (df["Location"] == ""))

    if special_no_loc.any():
        from collections import Counter
        # Dynamic lookup: creator -> most-common (Location, Incharge) in this file
        dyn_creator: dict = {}
        for creator, grp in df[df["Location"] != ""].groupby("Created By"):
            counts = Counter(zip(grp["Location"], grp["Accounts Incharge"]))
            if counts:
                dyn_creator[str(creator).strip()] = counts.most_common(1)[0][0]

        # Persistent map wins over dynamic (user-set values take priority)
        eff_creator = {**dyn_creator, **creator_map}

        new_discoveries: dict = {}
        filled = 0
        for idx in df[special_no_loc].index:
            creator = str(df.at[idx, "Created By"]).strip()
            if creator in eff_creator:
                loc, inc = eff_creator[creator]
                inc = _resolve_incharge(loc, inc, loc_incharge_map)
                df.at[idx, "Location"]          = loc
                df.at[idx, "Accounts Incharge"] = inc
                filled += 1
                # Save ANY resolved creator not already in the persistent map
                # (previously only saved those found via dyn_creator, missing
                #  creators whose ALL rows are OFFICE/HOME/TDS)
                if creator not in creator_map:
                    new_discoveries[creator] = (loc, inc)

        # Auto-save newly discovered creator mappings for future runs. Each
        # one is a targeted single-row upsert (not a read-modify-write of
        # the whole table), so two concurrent report jobs discovering
        # different new creators can never clobber each other's discovery -
        # see _upsert_creator_mapping's docstring.
        if new_discoveries:
            for _creator, (_loc, _inc) in new_discoveries.items():
                _upsert_creator_mapping(_creator, _loc, _inc)
            log_q.put(("ok",
                f"Saved {len(new_discoveries)} new creator mapping(s) to history"))

        if filled:
            log_q.put(("ok",
                f"Inferred Location & Accounts Incharge for {filled} "
                f"OFFICE / HOME / TDS row(s) via Created By"))
        still_empty = int(special_no_loc.sum()) - filled
        if still_empty:
            log_q.put(("warn",
                f"{still_empty} OFFICE/HOME/TDS row(s) still unresolved "
                f"-- Created By not found in data or history"))
    matched   = int((df["Location"] != "").sum())
    unmatched = int((df["Location"] == "").sum())
    log_q.put(("ok", f"Location & Accounts Incharge mapped for {format_indian_number(matched)} / {format_indian_number(len(df))} rows"))
    if unmatched:
        missing_sites = (
            df[df["Location"] == ""]["Supplier Site"]
            .value_counts()
            .head(10)
        )
        log_q.put(("warn", f"{format_indian_number(unmatched)} row(s) have no mapping — top unmapped sites:"))
        for site, cnt in missing_sites.items():
            log_q.put(("warn", f"    {site!r}  ({cnt} invoice(s))"))

    log_q.put(("ok", f"Done  →  {format_indian_number(len(df))} rows  ·  {format_indian_number(len(df.columns))} columns"))
    return df, total_rows, input_cols, matched


def process_report_multi(input_paths: list, log_q) -> tuple:
    """Merge multiple Unaccounted Transactions XLS files and process them.

    Each file is read individually so that its own Period Name (from the B12
    header cell, e.g. 'Jun-26') can be extracted and stamped on every row
    from that file as ``__month__``.  This gives the pivot table the correct
    month break-down even when files span different accounting periods.

    Returns ``(df, total_rows, input_cols, matched)`` — same signature as
    :func:`process_report`.
    """
    if len(input_paths) == 1:
        # Fast path: single file handled by process_report (which also sets __month__)
        return process_report(input_paths[0], log_q)

    log_q.put(("info", f"Merging {len(input_paths)} input file(s) …"))

    # ── Read each file individually, tagging rows with its own Period Name ────
    mini_dfs = []
    for idx, path in enumerate(input_paths):
        log_q.put(("info", f"Reading file {idx + 1}/{len(input_paths)}: {os.path.basename(path)} …"))
        tables = _read_export_tables(path)
        raw = find_data_table(tables)
        if raw is None:
            raise JobUserError(f"Could not locate data table in {os.path.basename(path)}")

        period = _extract_period_name(tables) or today_ist().strftime("%b-%y")

        headers  = [str(v).strip() for v in raw.iloc[1].values]
        mini_df  = raw.iloc[2:].copy()
        mini_df.columns = headers
        mini_df  = mini_df.reset_index(drop=True)
        mini_df["__month__"] = period

        log_q.put(("ok", f"  → {format_indian_number(len(mini_df))} rows from {os.path.basename(path)} (Period: {period})"))
        mini_dfs.append(mini_df)

    df = pd.concat(mini_dfs, ignore_index=True)
    log_q.put(("info", f"Merged {format_indian_number(len(input_paths))} file(s) → {format_indian_number(len(df))} total data rows"))

    total_rows = len(df)
    input_cols = len([c for c in df.columns if c != "__month__"])
    log_q.put(("info", f"Rows: {format_indian_number(total_rows)}  ·  Columns before: {format_indian_number(input_cols)}"))

    # Drop unwanted columns (ignore if already missing)
    existing_drops = [c for c in COLS_TO_DROP if c in df.columns]
    df = df.drop(columns=existing_drops)
    log_q.put(("ok", f"Columns deleted: {existing_drops}"))

    # Move Supplier Site to the end (keep __month__ out of the reorder)
    if COL_MOVE_TO_END in df.columns:
        non_month = [c for c in df.columns if c not in (COL_MOVE_TO_END, "__month__")]
        non_month.append(COL_MOVE_TO_END)
        non_month.append("__month__")
        df = df[non_month]
        log_q.put(("ok", f"'{COL_MOVE_TO_END}' moved to last column"))

    # Load custom overrides + persistent creator map from mappings.json
    site_overrides, creator_map = _load_custom_mappings()
    loc_incharge_map = _load_location_incharge()
    eff_site = {**SITE_MAPPING, **site_overrides}

    df["Location"]          = df[COL_MOVE_TO_END].apply(
        lambda s: eff_site.get(str(s).strip(), ("", ""))[0])
    df["Accounts Incharge"] = df[COL_MOVE_TO_END].apply(
        lambda s: _resolve_incharge(*eff_site.get(str(s).strip(), ("", "")), loc_incharge_map))

    if site_overrides:
        log_q.put(("info", f"Applied {len(site_overrides)} custom site override(s)"))

    # For OFFICE / HOME / TDS TAX Authori: creator-based inference
    SPECIAL_SITES  = {"OFFICE", "HOME", "TDS TAX Authori"}
    special_no_loc = (df["Supplier Site"].astype(str).str.strip().isin(SPECIAL_SITES)
                      & (df["Location"] == ""))

    if special_no_loc.any():
        from collections import Counter
        dyn_creator: dict = {}
        for creator, grp in df[df["Location"] != ""].groupby("Created By"):
            counts = Counter(zip(grp["Location"], grp["Accounts Incharge"]))
            if counts:
                dyn_creator[str(creator).strip()] = counts.most_common(1)[0][0]

        eff_creator = {**dyn_creator, **creator_map}

        new_discoveries: dict = {}
        filled = 0
        for idx in df[special_no_loc].index:
            creator = str(df.at[idx, "Created By"]).strip()
            if creator in eff_creator:
                loc, inc = eff_creator[creator]
                inc = _resolve_incharge(loc, inc, loc_incharge_map)
                df.at[idx, "Location"]          = loc
                df.at[idx, "Accounts Incharge"] = inc
                filled += 1
                if creator not in creator_map:
                    new_discoveries[creator] = (loc, inc)

        if new_discoveries:
            for _creator, (_loc, _inc) in new_discoveries.items():
                _upsert_creator_mapping(_creator, _loc, _inc)
            log_q.put(("ok",
                f"Saved {len(new_discoveries)} new creator mapping(s) to history"))

        if filled:
            log_q.put(("ok",
                f"Inferred Location & Accounts Incharge for {filled} "
                f"OFFICE / HOME / TDS row(s) via Created By"))
        still_empty = int(special_no_loc.sum()) - filled
        if still_empty:
            log_q.put(("warn",
                f"{still_empty} OFFICE/HOME/TDS row(s) still unresolved "
                f"-- Created By not found in data or history"))

    matched   = int((df["Location"] != "").sum())
    unmatched = int((df["Location"] == "").sum())
    log_q.put(("ok", f"Location & Accounts Incharge mapped for {format_indian_number(matched)} / {format_indian_number(len(df))} rows"))
    if unmatched:
        missing_sites = (
            df[df["Location"] == ""]["Supplier Site"]
            .value_counts()
            .head(10)
        )
        log_q.put(("warn", f"{format_indian_number(unmatched)} row(s) have no mapping — top unmapped sites:"))
        for site, cnt in missing_sites.items():
            log_q.put(("warn", f"    {site!r}  ({cnt} invoice(s))"))

    log_q.put(("ok", f"Done  →  {format_indian_number(len(df))} rows  ·  {format_indian_number(len(df.columns))} columns"))
    return df, total_rows, input_cols, matched


# ── Uninvoiced Expense PO Report ──────────────────────────────────────────────

def _po_norm(s: str) -> str:
    """Lowercase, keep only alphanumeric chars — used for fuzzy keyword matching."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _fuzzy_keyword_match(description: str, keyword: str, threshold: float = 0.82) -> bool:
    """Return True if *keyword* fuzzy-matches anywhere inside *description*.

    Two checks:
    1. Exact substring match after normalisation (handles 'landrent' == 'land rent').
    2. Sliding-window SequenceMatcher for single-char typos ('lant rent').
    """
    desc_n = _po_norm(str(description))
    kw_n   = _po_norm(str(keyword))
    if not kw_n or not desc_n:
        return False
    if kw_n in desc_n:
        return True
    n = len(kw_n)
    for i in range(len(desc_n) - n + 1):
        if SequenceMatcher(None, kw_n, desc_n[i:i + n]).ratio() >= threshold:
            return True
    return False


def find_po_data_table(tables: list) -> "pd.DataFrame | None":
    """Return the table whose row 0 contains PO_ANCHOR_COL."""
    for df in tables:
        if df.shape[0] >= 1:
            row0 = [str(v).strip() for v in df.iloc[0].values]
            if PO_ANCHOR_COL in row0:
                return df
    return None


def detect_po_periods(input_path: str) -> list:
    """Return sorted unique Month values (Mon-YY) from the PO file."""
    try:
        tables = pd.read_html(input_path, flavor="lxml")
        raw    = find_po_data_table(tables)
        if raw is None:
            return []
        headers = [str(v).strip() for v in raw.iloc[0].values]
        df      = raw.iloc[1:].copy()
        df.columns = headers
        if PO_APPROVED_DATE not in df.columns:
            return []

        def _to_month(val):
            s = str(val).strip().split(",")[0].strip()
            try:
                return pd.to_datetime(s, dayfirst=True).strftime("%b-%y")
            except Exception:
                return None

        months = df[PO_APPROVED_DATE].apply(_to_month).dropna().unique().tolist()

        def _month_key(m):
            try:
                return pd.to_datetime(m, format="%b-%y")
            except Exception:
                return pd.Timestamp.max

        return sorted([m for m in months if m], key=_month_key)
    except Exception as e:
        import traceback
        print(f"[WARN] detect_po_periods failed: {e}\n{traceback.format_exc()}")
        return []


def detect_po_numbers(input_path: str) -> list:
    """Return sorted unique PO numbers from the file (as strings)."""
    try:
        tables = pd.read_html(input_path, flavor="lxml")
        raw    = find_po_data_table(tables)
        if raw is None:
            return []
        headers = [str(v).strip() for v in raw.iloc[0].values]
        df      = raw.iloc[1:].copy()
        df.columns = headers
        if PO_ANCHOR_COL not in df.columns:
            return []
        nums = (df[PO_ANCHOR_COL].dropna()
                .astype(str).str.strip()
                .unique().tolist())
        return sorted(nums)
    except Exception as e:
        import traceback
        print(f"[WARN] detect_po_numbers failed: {e}\n{traceback.format_exc()}")
        return []


def process_po_report(
    input_path: str,
    exclude_months: set,
    keywords: list,
    log_q,
    fuzzy_threshold: float = 0.82,
) -> tuple:
    """Read Uninvoiced Expense PO XLS and return
    (main_df, moved_df, unmapped_df, total_rows, input_cols, matched).

    Columns added:
      - Month  (after PO Approved Date)
      - Location, Accounts Incharge  (after Organization Name)

    Rows routed to separate outputs:
      - unmapped_df : Location lookup returned "" (→ 'Mapping Not Found' sheet)
      - moved_df    : Header Description fuzzy-matches a keyword, OR PO Number
                      is in the persisted excluded-POs list (→ 'Excluded POs' sheet)
      - main_df     : everything else
    """
    log_q.put(("info", "Reading Uninvoiced Expense PO file …"))
    tables = _read_export_tables(input_path)
    raw    = find_po_data_table(tables)
    if raw is None:
        raise JobUserError(
            f"Could not locate PO data table in {os.path.basename(input_path)}")

    headers    = [str(v).strip() for v in raw.iloc[0].values]
    df         = raw.iloc[1:].copy()
    df.columns = headers
    df         = df.reset_index(drop=True)

    total_rows = len(df)
    input_cols = len(df.columns)
    log_q.put(("info", f"Rows: {format_indian_number(total_rows)}  ·  Columns before: {format_indian_number(input_cols)}"))

    # ── Month column ──────────────────────────────────────────────────────────
    def _parse_month(val):
        s = str(val).strip().split(",")[0].strip()
        try:
            return pd.to_datetime(s, dayfirst=True).strftime("%b-%y")
        except Exception:
            return ""

    if PO_APPROVED_DATE in df.columns:
        idx = list(df.columns).index(PO_APPROVED_DATE)
        df.insert(idx + 1, "Month", df[PO_APPROVED_DATE].apply(_parse_month))
        log_q.put(("ok", "Added 'Month' column after 'PO Approved Date'"))

    # ── Filter by month ───────────────────────────────────────────────────────
    if exclude_months:
        before = len(df)
        df = df[~df["Month"].isin(exclude_months)].reset_index(drop=True)
        log_q.put(("info",
            f"Removed {format_indian_number(before - len(df))} rows for month(s): "
            f"{', '.join(sorted(exclude_months))}"))

    # ── Location & Accounts Incharge ──────────────────────────────────────────
    site_overrides, _cr = _load_custom_mappings()
    loc_incharge_map = _load_location_incharge()
    eff_site = {**SITE_MAPPING, **site_overrides}

    if PO_SITE_COL in df.columns and PO_ORG_COL in df.columns:
        org_idx = list(df.columns).index(PO_ORG_COL)
        df.insert(org_idx + 1, "Location",
                  df[PO_SITE_COL].apply(lambda s: eff_site.get(str(s).strip(), ("", ""))[0]))
        df.insert(org_idx + 2, "Accounts Incharge",
                  df[PO_SITE_COL].apply(
                      lambda s: _resolve_incharge(*eff_site.get(str(s).strip(), ("", "")), loc_incharge_map)))
        log_q.put(("ok", "Added 'Location' and 'Accounts Incharge' after 'Organization Name'"))
    else:
        log_q.put(("warn", f"'{PO_SITE_COL}' or '{PO_ORG_COL}' not found — skipping mapping"))

    matched   = int((df["Location"] != "").sum()) if "Location" in df.columns else 0
    unmatched = len(df) - matched
    log_q.put(("ok", f"Location mapped for {format_indian_number(matched)} / {format_indian_number(len(df))} rows"))

    # ── Separate unmapped rows → 'Mapping Not Found' sheet ───────────────────
    if "Location" in df.columns and unmatched > 0:
        unmap_mask  = df["Location"].astype(str).str.strip() == ""
        unmapped_df = df[unmap_mask].reset_index(drop=True)
        df          = df[~unmap_mask].reset_index(drop=True)
        missing = (unmapped_df[PO_SITE_COL].value_counts().head(8)
                   if PO_SITE_COL in unmapped_df.columns else pd.Series())
        for site, cnt in missing.items():
            log_q.put(("warn", f"    {site!r}  ({cnt} row(s)) — unmapped"))
        log_q.put(("warn",
            f"{format_indian_number(unmatched)} unmapped row(s) moved to 'Mapping Not Found' sheet"))
    else:
        unmapped_df = pd.DataFrame(columns=df.columns)

    # ── Load persisted excluded PO numbers ───────────────────────────────────
    from .mappings import _load_po_excluded
    exclude_po_numbers = set(_load_po_excluded())
    if exclude_po_numbers:
        log_q.put(("info",
            f"Excluding {len(exclude_po_numbers)} persisted PO number(s)"))

    # ── Keyword filter on Header Description ─────────────────────────────────
    kw_mask = pd.Series(False, index=df.index)
    if keywords and PO_HDR_COL in df.columns:
        kw_norms = [_po_norm(kw) for kw in keywords if _po_norm(kw)]
        if kw_norms:
            desc_norms = df[PO_HDR_COL].astype(str).apply(_po_norm)
            thr = fuzzy_threshold
            def _any_kw(desc_n, _kns=kw_norms, _t=thr):
                if not desc_n:
                    return False
                for kw_n in _kns:
                    if kw_n in desc_n:
                        return True
                    n = len(kw_n)
                    for i in range(len(desc_n) - n + 1):
                        if SequenceMatcher(None, kw_n, desc_n[i:i + n]).ratio() >= _t:
                            return True
                return False
            kw_mask = desc_norms.apply(_any_kw)
        log_q.put(("info",
            f"Keyword filter matched {format_indian_number(int(kw_mask.sum()))} row(s) "
            f"(threshold={fuzzy_threshold:.2f}, moved to Excluded POs)"))

    # ── PO number filter ──────────────────────────────────────────────────────
    po_mask = pd.Series(False, index=df.index)
    if exclude_po_numbers and PO_ANCHOR_COL in df.columns:
        po_mask = df[PO_ANCHOR_COL].astype(str).str.strip().isin(
            {str(p).strip() for p in exclude_po_numbers})
        log_q.put(("info",
            f"Excluded PO filter matched {format_indian_number(int(po_mask.sum()))} row(s) "
            f"(moved to Excluded POs)"))

    move_mask = kw_mask | po_mask
    moved_df  = df[move_mask].reset_index(drop=True)
    main_df   = df[~move_mask].reset_index(drop=True)

    log_q.put(("ok",
        f"Done  →  Main: {format_indian_number(len(main_df))}  ·  "
        f"Excluded POs: {format_indian_number(len(moved_df))}  ·  "
        f"Mapping Not Found: {format_indian_number(len(unmapped_df))}  ·  "
        f"{len(main_df.columns)} columns"))
    return main_df, moved_df, unmapped_df, total_rows, input_cols, matched


# ── MRN Report processing ──────────────────────────────────────────────────────
def find_mrn_data_table(tables: list) -> "pd.DataFrame | None":
    """Return the table whose first row contains MRN_ANCHOR_COL."""
    for df in tables:
        if df.shape[0] >= 1:
            row0_vals = [str(v).strip() for v in df.iloc[0].values]
            if MRN_ANCHOR_COL in row0_vals:
                return df
    return None


def detect_mrn_periods(input_path: str) -> list:
    """Return chronologically-sorted unique accounting periods from an MRN XLS file."""
    try:
        tables = pd.read_html(input_path, flavor="lxml")
        raw    = find_mrn_data_table(tables)
        if raw is None:
            return []
        headers    = [str(v).strip() for v in raw.iloc[0].values]
        df         = raw.iloc[1:].copy()
        df.columns = headers
        if MRN_ANCHOR_COL not in df.columns:
            return []
        periods = df[MRN_ANCHOR_COL].dropna().unique().tolist()

        def _period_key(p):
            try:
                return pd.to_datetime(str(p), format="%b-%Y")
            except Exception:
                return pd.Timestamp.max

        return sorted([str(p) for p in periods], key=_period_key)
    except Exception as e:
        import traceback
        print(f"[WARN] detect_mrn_periods failed: {e}\n{traceback.format_exc()}")
        return []


def process_mrn_report(input_path: str, exclude_periods: set, log_q) -> tuple:
    """Read Pending MRN XLS, add Location + Accounts Incharge, filter periods."""
    log_q.put(("info", "Reading MRN file …"))
    tables = _read_export_tables(input_path)
    raw    = find_mrn_data_table(tables)
    if raw is None:
        raise JobUserError(
            f"Could not locate MRN data table in {os.path.basename(input_path)}")

    # Row 0 = column headers (MRN has no merged-title first row unlike Report 1)
    headers    = [str(v).strip() for v in raw.iloc[0].values]
    df         = raw.iloc[1:].copy()
    df.columns = headers
    df         = df.reset_index(drop=True)

    total_rows = len(df)
    input_cols = len(df.columns)
    log_q.put(("info", f"Rows: {format_indian_number(total_rows)}  ·  Columns before: {format_indian_number(input_cols)}"))

    # Filter excluded accounting periods
    if exclude_periods:
        before = len(df)
        df = df[~df[MRN_ANCHOR_COL].isin(exclude_periods)].reset_index(drop=True)
        removed = before - len(df)
        log_q.put(("info",
            f"Removed {format_indian_number(removed)} rows for period(s): "
            f"{', '.join(sorted(exclude_periods))}"))

    # Reformat ACCOUNTING PERIOD to Mon-YY  (e.g. "FEB-2026" → "Feb-26").
    # Done AFTER period filtering above, which matches the raw "FEB-2026" values.
    if MRN_ANCHOR_COL in df.columns:
        def _fmt_period(p):
            s = str(p).strip()
            try:
                return pd.to_datetime(s, format="%b-%Y").strftime("%b-%y")
            except Exception:
                return s
        df[MRN_ANCHOR_COL] = df[MRN_ANCHOR_COL].apply(_fmt_period)
        log_q.put(("ok", "Formatted 'ACCOUNTING PERIOD' as Mon-YY"))

    # Load shared mappings (same SITE_MAPPING + custom overrides as Report 1)
    site_overrides, _cr = _load_custom_mappings()
    loc_incharge_map = _load_location_incharge()
    eff_site = {**SITE_MAPPING, **site_overrides}

    # Insert Location + Accounts Incharge immediately after SUPPLIER SITE
    if MRN_SITE_COL in df.columns:
        site_idx = list(df.columns).index(MRN_SITE_COL)
        df.insert(site_idx + 1, "Location",
                  df[MRN_SITE_COL].apply(lambda s: eff_site.get(str(s).strip(), ("", ""))[0]))
        df.insert(site_idx + 2, "Accounts Incharge",
                  df[MRN_SITE_COL].apply(
                      lambda s: _resolve_incharge(*eff_site.get(str(s).strip(), ("", "")), loc_incharge_map)))
        if site_overrides:
            log_q.put(("info", f"Applied {len(site_overrides)} custom site override(s)"))
    else:
        log_q.put(("warn", f"Column '{MRN_SITE_COL}' not found — skipping mapping"))

    matched   = int((df["Location"] != "").sum())
    unmatched = int((df["Location"] == "").sum())

    if unmatched:
        missing_sites = (
            df[df["Location"] == ""][MRN_SITE_COL]
            .value_counts()
            .head(10)
            .to_dict()
        )
        log_q.put(("warn",
            f"{format_indian_number(unmatched)} row(s) have no mapping — top unmapped sites:"))
        for site, cnt in missing_sites.items():
            log_q.put(("warn", f"    {site!r}  ({cnt} row(s))"))

    log_q.put(("ok", f"Done  →  {format_indian_number(len(df))} rows  ·  {format_indian_number(len(df.columns))} columns"))
    return df, total_rows, input_cols, matched
