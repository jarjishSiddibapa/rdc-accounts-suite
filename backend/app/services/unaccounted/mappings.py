"""Persistent custom-mapping helpers — MySQL-backed via SQLAlchemy.

Ported from the desktop app's mappings.py, which read/wrote JSON files under
%APPDATA%\\UnaccountedTransactions\\. The suite now shares one MySQL database
(app.database), so every function here reads/writes rows in the tables
defined in .models instead.

Every public function keeps the SAME signature shape it always had, plus one
new *optional* trailing ``db: Session = None`` parameter: pass a request-scoped
session from a router to reuse one connection across several calls, or omit it
entirely to let the function open and close its own short-lived SessionLocal()
— which is exactly what processing.py's existing call sites do (they call
these with zero arguments, unchanged, since they predate the DB migration).

site_mapping.py's ~1,131-entry SITE_MAPPING dict stays a hardcoded Python
constant — it is not stored in any table here; processing.py merges it with
these DB-backed overrides at read time exactly as before.
"""

import json
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import Session

from app import config
from app.database import SessionLocal
from app.soft_delete import (
    delete_keyed_row,
    list_archived_rows,
    restore_keyed_row,
    seed_missing_keyed_rows,
    sync_keyed_rows,
    upsert_keyed_row,
)
from .models import (
    CreatorMapping,
    ExcludedPo,
    LocationIncharge,
    PoKeyword,
    PoKeywordSettings,
    SiteOverride,
)


@contextmanager
def _session_scope(db: "Session | None"):
    """Yield *db* unchanged if given; otherwise open + close a fresh
    SessionLocal() for the duration of the `with` block.

    Always commits on success, regardless of who opened the session: a
    request-scoped session from a router's Depends(get_db) is never
    committed by get_db() itself (see app/database.py's get_db - it only
    closes, on the assumption each caller commits its own writes), and none
    of this router's call sites called db.commit() after these functions
    either. The result was a real, reproduced-in-production bug: every
    /mappings/fix (and every Add/Edit/Delete/Restore on this tool's
    3 mapping tables) returned 200 OK and looked saved, but the write was
    silently rolled back the moment the request ended - so a report
    regenerated right after "fixing" a mapping still showed it as missing,
    forever, no matter how many times it was "fixed". Committing here,
    unconditionally, is what every other app's mapping_store.py already
    does (each of their upsert/delete functions calls db.commit() directly)
    - only session *ownership* (who calls close()) should depend on who
    opened it, not whether the write actually persists.
    """
    owns = db is None
    session = db or SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if owns:
            session.close()


# ── Site overrides + Created-By mapping ────────────────────────────────────────

def _load_custom_mappings(db: "Session | None" = None) -> tuple:
    """Return (site_overrides, creator_map)  –  both are  {str: (str, str)}."""
    with _session_scope(db) as s:
        site_ov = {
            row.supplier_site: (row.location or "", row.accounts_incharge or "")
            for row in s.query(SiteOverride).filter(SiteOverride.is_deleted == False).all()  # noqa: E712
        }
        creator = {
            row.created_by: (row.location or "", row.accounts_incharge or "")
            for row in s.query(CreatorMapping).filter(CreatorMapping.is_deleted == False).all()  # noqa: E712
        }
    return site_ov, creator


def _save_site_overrides(site_overrides: dict, db: "Session | None" = None) -> None:
    """Persist ONLY the site-overrides table — full sync, soft-delete-aware
    (see app/soft_delete.py): a key missing from the given dict is flagged
    is_deleted, never actually removed; a key that reappears (even if
    previously soft-deleted) is revived and updated in place."""
    site_overrides = {
        k.strip(): (v[0].strip(), v[1].strip())
        for k, v in site_overrides.items() if k.strip()
    }
    with _session_scope(db) as s:
        sync_keyed_rows(s, SiteOverride, ("supplier_site",), {
            site: {"location": loc, "accounts_incharge": inc}
            for site, (loc, inc) in site_overrides.items()
        })
        s.flush()


def _save_creator_map(creator_map: dict, db: "Session | None" = None) -> None:
    """Persist ONLY the created-by mapping table — same full-sync,
    soft-delete-aware semantics as _save_site_overrides, but deliberately
    separate: a caller that only wants to record newly-learned creator
    mappings (see processing.py's dynamic creator-inference) must never be
    able to touch site_overrides as a side effect, even accidentally with a
    stale/incomplete snapshot of it. A real incident: process_report(_multi)
    used to call a combined _save_custom_mappings(site_overrides,
    updated_creator) here, and every one of its 26 site overrides + 19
    creator mappings ended up soft-deleted at once - that whole class of bug
    is now structurally impossible, since this function never even receives
    a site_overrides argument to mishandle."""
    creator_map = {
        k.strip(): (v[0].strip(), v[1].strip())
        for k, v in creator_map.items() if k.strip()
    }
    with _session_scope(db) as s:
        sync_keyed_rows(s, CreatorMapping, ("created_by",), {
            creator: {"location": loc, "accounts_incharge": inc}
            for creator, (loc, inc) in creator_map.items()
        })
        s.flush()


def _upsert_site_override(
    supplier_site: str, location: str, accounts_incharge: str, db: "Session | None" = None,
) -> None:
    """Insert/update exactly ONE Supplier Site override - safe to call
    repeatedly back-to-back for different sites (e.g. fixing several
    unmapped sites from the missing-mapping panel) without racing another
    in-flight fix, unlike _save_site_overrides' full-table sync."""
    supplier_site = supplier_site.strip()
    with _session_scope(db) as s:
        upsert_keyed_row(s, SiteOverride, ("supplier_site",), supplier_site, {
            "location": location.strip(),
            "accounts_incharge": accounts_incharge.strip(),
        })
        s.flush()


def _delete_site_override(supplier_site: str, db: "Session | None" = None) -> bool:
    with _session_scope(db) as s:
        return delete_keyed_row(s, SiteOverride, ("supplier_site",), supplier_site.strip())


def _list_archived_site_overrides(db: "Session | None" = None) -> list:
    with _session_scope(db) as s:
        return [
            {"supplier_site": r.supplier_site, "location": r.location or "",
             "accounts_incharge": r.accounts_incharge or ""}
            for r in list_archived_rows(s, SiteOverride)
        ]


def _restore_site_override(supplier_site: str, db: "Session | None" = None) -> bool:
    with _session_scope(db) as s:
        return restore_keyed_row(s, SiteOverride, ("supplier_site",), supplier_site.strip())


def _upsert_creator_mapping(
    created_by: str, location: str, accounts_incharge: str, db: "Session | None" = None,
) -> None:
    created_by = created_by.strip()
    with _session_scope(db) as s:
        upsert_keyed_row(s, CreatorMapping, ("created_by",), created_by, {
            "location": location.strip(),
            "accounts_incharge": accounts_incharge.strip(),
        })
        s.flush()


def _delete_creator_mapping(created_by: str, db: "Session | None" = None) -> bool:
    with _session_scope(db) as s:
        return delete_keyed_row(s, CreatorMapping, ("created_by",), created_by.strip())


def _list_archived_creator_mappings(db: "Session | None" = None) -> list:
    with _session_scope(db) as s:
        return [
            {"created_by": r.created_by, "location": r.location or "",
             "accounts_incharge": r.accounts_incharge or ""}
            for r in list_archived_rows(s, CreatorMapping)
        ]


def _restore_creator_mapping(created_by: str, db: "Session | None" = None) -> bool:
    with _session_scope(db) as s:
        return restore_keyed_row(s, CreatorMapping, ("created_by",), created_by.strip())


def _upsert_location_incharge(
    location: str, accounts_incharge: str, db: "Session | None" = None,
) -> None:
    location = location.strip()
    with _session_scope(db) as s:
        upsert_keyed_row(s, LocationIncharge, ("location",), location, {
            "accounts_incharge": accounts_incharge.strip(),
        })
        s.flush()


def _delete_location_incharge(location: str, db: "Session | None" = None) -> bool:
    with _session_scope(db) as s:
        return delete_keyed_row(s, LocationIncharge, ("location",), location.strip())


def _list_archived_location_incharge(db: "Session | None" = None) -> list:
    with _session_scope(db) as s:
        return [
            {"location": r.location, "accounts_incharge": r.accounts_incharge or ""}
            for r in list_archived_rows(s, LocationIncharge)
        ]


def _restore_location_incharge(location: str, db: "Session | None" = None) -> bool:
    with _session_scope(db) as s:
        return restore_keyed_row(s, LocationIncharge, ("location",), location.strip())


def _save_custom_mappings(site_overrides: dict, creator_map: dict, db: "Session | None" = None) -> None:
    """Persist BOTH mapping tables in one call - only for callers that
    genuinely intend to update both (the mapping-fix/CRUD endpoints, which
    always load-merge-save the full current state of both). Prefer
    _save_site_overrides/_save_creator_map directly wherever a caller only
    means to touch one of them."""
    with _session_scope(db) as s:
        _save_site_overrides(site_overrides, s)
        _save_creator_map(creator_map, s)


# ── Location <-> Accounts Incharge (one-time table) ───────────────────────────

def _load_location_incharge(db: "Session | None" = None) -> dict:
    """Return {Location: Accounts Incharge}. Starts empty and is populated
    entirely by the user over time (via the mapping-fix flow or the
    Location<->Incharge editor) — there is no auto-seed from SITE_MAPPING."""
    with _session_scope(db) as s:
        return {
            row.location: row.accounts_incharge
            for row in s.query(LocationIncharge).filter(LocationIncharge.is_deleted == False).all()  # noqa: E712
        }


def _save_location_incharge(location_incharge: dict, db: "Session | None" = None) -> None:
    """Persist the Location -> Accounts Incharge table (full sync,
    soft-delete-aware)."""
    location_incharge = {
        k.strip(): str(v).strip()
        for k, v in location_incharge.items() if k.strip()
    }
    with _session_scope(db) as s:
        sync_keyed_rows(s, LocationIncharge, ("location",), {
            loc: {"accounts_incharge": inc} for loc, inc in location_incharge.items()
        })
        s.flush()


def _known_locations(db: "Session | None" = None) -> list:
    """Distinct Location values pooled across every mapping table, for
    'pick existing or type new' pickers."""
    from .site_mapping import SITE_MAPPING
    site_ov, creator = _load_custom_mappings(db)
    loc_inc = _load_location_incharge(db)
    vals = {loc.strip() for loc, _ in SITE_MAPPING.values()}
    vals |= {loc.strip() for loc, _ in site_ov.values()}
    vals |= {loc.strip() for loc, _ in creator.values()}
    vals |= {loc.strip() for loc in loc_inc.keys()}
    vals.discard("")
    return sorted(vals, key=str.lower)


def _known_incharges(db: "Session | None" = None) -> list:
    """Distinct Accounts Incharge values pooled across every mapping table,
    for 'pick existing or type new' pickers."""
    from .site_mapping import SITE_MAPPING
    site_ov, creator = _load_custom_mappings(db)
    loc_inc = _load_location_incharge(db)
    vals = {inc.strip() for _, inc in SITE_MAPPING.values()}
    vals |= {inc.strip() for _, inc in site_ov.values()}
    vals |= {inc.strip() for _, inc in creator.values()}
    vals |= {inc.strip() for inc in loc_inc.values()}
    vals.discard("")
    return sorted(vals, key=str.lower)


# ── PO keyword helpers ────────────────────────────────────────────────────────

PO_DEFAULT_KEYWORDS = ["land rent", "room rent", "guest", "ground rent"]
LEGACY_SEED_PATH = config.SEED_DIR / "unaccounted-mappings.json"


def _load_po_keywords(db: "Session | None" = None) -> list:
    """Return list of fuzzy-filter keywords for PO report. Falls back to
    PO_DEFAULT_KEYWORDS when the table is empty."""
    with _session_scope(db) as s:
        rows = (
            s.query(PoKeyword)
            .filter(PoKeyword.is_deleted == False)  # noqa: E712
            .order_by(PoKeyword.id)
            .all()
        )
        kws = [r.keyword for r in rows]
    return kws if kws else list(PO_DEFAULT_KEYWORDS)


def _save_po_keywords(keywords: list, db: "Session | None" = None) -> None:
    """Persist keyword list (full sync, soft-delete-aware). Duplicates
    (after normalising to lowercase/stripped) are collapsed to one row each,
    since PoKeyword.keyword is unique — order is preserved for the first
    occurrence of each."""
    seen: dict[str, None] = {}
    for k in keywords:
        k = k.strip().lower()
        if k:
            seen.setdefault(k, None)
    keywords = list(seen.keys())
    with _session_scope(db) as s:
        sync_keyed_rows(s, PoKeyword, ("keyword",), {kw: {} for kw in keywords})
        s.flush()


def _load_po_threshold(db: "Session | None" = None) -> float:
    """Return saved fuzzy match threshold (default 0.82)."""
    with _session_scope(db) as s:
        row = s.query(PoKeywordSettings).filter(PoKeywordSettings.id == 1).first()
        if row is None:
            return 0.82
        return float(max(0.50, min(1.00, row.threshold)))


def _save_po_threshold(threshold: float, db: "Session | None" = None) -> None:
    """Persist fuzzy threshold as the single settings row (id=1)."""
    with _session_scope(db) as s:
        row = s.query(PoKeywordSettings).filter(PoKeywordSettings.id == 1).first()
        if row is None:
            s.add(PoKeywordSettings(id=1, threshold=round(float(threshold), 2)))
        else:
            row.threshold = round(float(threshold), 2)
        s.flush()


# ── PO excluded PO numbers ────────────────────────────────────────────────────

def _load_po_excluded(db: "Session | None" = None) -> list:
    """Return list of PO numbers permanently excluded from the main PO report."""
    with _session_scope(db) as s:
        return [
            r.po_number
            for r in s.query(ExcludedPo)
            .filter(ExcludedPo.is_deleted == False)  # noqa: E712
            .order_by(ExcludedPo.id)
            .all()
        ]


def _save_po_excluded(excluded: list, db: "Session | None" = None) -> None:
    """Persist excluded PO numbers list (full sync, soft-delete-aware).
    Duplicate PO numbers in the input are collapsed to one row each, since
    ExcludedPo.po_number is unique."""
    seen: dict[str, None] = {}
    for p in excluded:
        p = str(p).strip()
        if p:
            seen.setdefault(p, None)
    excluded = list(seen.keys())
    with _session_scope(db) as s:
        sync_keyed_rows(s, ExcludedPo, ("po_number",), {po: {} for po in excluded})
        s.flush()


def seed_missing_from_json(
    db: Session,
    path: "str | Path" = LEGACY_SEED_PATH,
) -> dict[str, int]:
    """Add legacy Uninvoiced/Unaccounted mappings without replacing admin data.

    Existing keys are skipped even when soft-deleted, so an administrator's
    edit or archive decision always wins.  The function commits once and is
    safe to run on every startup.
    """
    source = Path(path)
    count_names = (
        "site_overrides",
        "creator_mapping",
        "location_incharge",
        "po_keywords",
        "excluded_pos",
        "po_keyword_settings",
    )
    if not source.exists():
        return {name: 0 for name in count_names}

    payload = json.loads(source.read_text(encoding="utf-8-sig"))

    def pair_rows(values: dict, key_name: str) -> dict:
        rows = {}
        for raw_key, raw_value in values.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if not isinstance(raw_value, (list, tuple)) or len(raw_value) < 2:
                raise ValueError(f"Invalid {key_name} seed value for {key!r}")
            rows[key] = {
                "location": str(raw_value[0]).strip(),
                "accounts_incharge": str(raw_value[1]).strip(),
            }
        return rows

    site_rows = pair_rows(payload.get("site_overrides", {}), "site override")
    creator_rows = pair_rows(payload.get("creator_mapping", {}), "creator mapping")
    location_rows = {
        str(location).strip(): {"accounts_incharge": str(incharge).strip()}
        for location, incharge in payload.get("location_incharge", {}).items()
        if str(location).strip()
    }

    keyword_config = payload.get("po_keywords", {})
    keyword_rows = {
        str(keyword).strip().lower(): {}
        for keyword in keyword_config.get("keywords", [])
        if str(keyword).strip()
    }
    excluded_rows = {
        str(po_number).strip(): {}
        for po_number in payload.get("excluded_pos", [])
        if str(po_number).strip()
    }

    inserted = {
        "site_overrides": seed_missing_keyed_rows(
            db, SiteOverride, ("supplier_site",), site_rows
        ),
        "creator_mapping": seed_missing_keyed_rows(
            db, CreatorMapping, ("created_by",), creator_rows
        ),
        "location_incharge": seed_missing_keyed_rows(
            db, LocationIncharge, ("location",), location_rows
        ),
        "po_keywords": seed_missing_keyed_rows(
            db, PoKeyword, ("keyword",), keyword_rows
        ),
        "excluded_pos": seed_missing_keyed_rows(
            db, ExcludedPo, ("po_number",), excluded_rows
        ),
        "po_keyword_settings": 0,
    }

    if db.query(PoKeywordSettings).filter(PoKeywordSettings.id == 1).first() is None:
        threshold = float(keyword_config.get("threshold", 0.82))
        db.add(PoKeywordSettings(id=1, threshold=max(0.50, min(1.00, threshold))))
        inserted["po_keyword_settings"] = 1

    db.commit()
    return inserted
