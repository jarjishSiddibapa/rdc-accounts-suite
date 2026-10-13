"""CRUD for the Ultrafine Cashflow master data.

Rows reference each other by id, so a rename is one UPDATE and nothing else
needs touching. The API speaks in names; this module resolves them to ids
(case-insensitively) and back. Nothing is ever hard-deleted: "delete"
archives the row and creating the same name again revives it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

import re

from sqlalchemy.orm import Session

from .keys import casefold_key, clean_name, description_key
from .models import (
    CfLocation,
    DescriptionAlias,
    Location,
    Party,
    SharingGroup,
    SharingRatioLine,
    SharingRatioSet,
    SummaryGroup,
)


class MappingError(ValueError):
    """A user-correctable problem with a mapping edit."""


def _all(db: Session, model, include_deleted: bool = False) -> list:
    query = db.query(model)
    if not include_deleted:
        query = query.filter(model.is_deleted == False)  # noqa: E712
    return query.all()


def find_named(db: Session, model, name: str | None):
    """Row (active or archived) whose name matches case-insensitively."""
    target = casefold_key(name or "")
    if not target:
        return None
    for row in _all(db, model, include_deleted=True):
        if casefold_key(row.name) == target:
            return row
    return None


def require_name(value: str | None, label: str) -> str:
    name = clean_name(value or "")
    if not name:
        raise MappingError(f"{label} is required.")
    return name


def _resolve(db: Session, model, name: str | None, label: str, create: bool = False, **defaults):
    """Id of the active row called `name`; optionally create it."""
    if name is None or not clean_name(name):
        return None
    row = find_named(db, model, name)
    if row is None:
        if not create:
            raise MappingError(f"{label} '{clean_name(name)}' does not exist.")
        row = model(name=clean_name(name), **defaults)
        db.add(row)
        db.flush()
    elif row.is_deleted:
        if not create:
            raise MappingError(f"{label} '{row.name}' is archived.")
        row.is_deleted = False
    return row.id


def _upsert_named(db: Session, model, row_id: int | None, name: str, label: str, fields: dict[str, Any]):
    """Create (or revive) a named row, or update/rename row `row_id`."""
    clash = find_named(db, model, name)
    if row_id is None:
        if clash is not None and not clash.is_deleted:
            raise MappingError(f"{label} '{clash.name}' already exists.")
        row = clash or model(name=name)
        if clash is None:
            db.add(row)
        row.name = name
        row.is_deleted = False
    else:
        row = db.get(model, row_id)
        if row is None or row.is_deleted:
            raise MappingError(f"{label} not found.")
        if clash is not None and clash.id != row.id and not clash.is_deleted:
            raise MappingError(f"{label} '{clash.name}' already exists.")
        if clash is not None and clash.id != row.id and clash.is_deleted:
            raise MappingError(f"{label} '{clash.name}' exists in the archive - restore it instead.")
        row.name = name
    for column, value in fields.items():
        setattr(row, column, value)
    db.flush()
    return row


def _archive(db: Session, model, row_id: int, label: str) -> None:
    row = db.get(model, row_id)
    if row is None or row.is_deleted:
        raise MappingError(f"{label} not found.")
    row.is_deleted = True
    db.flush()


# ── summary groups ──────────────────────────────────────────────────────────

def list_summary_groups(db: Session) -> list[dict]:
    return [{"id": r.id, "name": r.name} for r in sorted(_all(db, SummaryGroup), key=lambda r: r.name.casefold())]


def save_summary_group(db: Session, row_id: int | None, name: str) -> dict:
    row = _upsert_named(db, SummaryGroup, row_id, require_name(name, "Group name"), "Group", {})
    return {"id": row.id, "name": row.name}


def archive_summary_group(db: Session, row_id: int) -> None:
    if any(r.group_id == row_id for r in _all(db, CfLocation)):
        raise MappingError("This group is still used by a CF location.")
    _archive(db, SummaryGroup, row_id, "Group")


# ── CF locations ────────────────────────────────────────────────────────────

def _cf_dict(row: CfLocation, groups: dict[int, str]) -> dict:
    return {
        "id": row.id, "name": row.name, "sort_order": row.sort_order,
        "show_in_summary": bool(row.show_in_summary),
        "authorized_person": row.authorized_person or "",
        "payable_pct": float(row.payable_pct),
        "reserve_pct": float(row.reserve_pct) if row.reserve_pct is not None else None,
        "group": groups.get(row.group_id) or "",
    }


def list_cf_locations(db: Session) -> list[dict]:
    groups = {g.id: g.name for g in _all(db, SummaryGroup)}
    rows = sorted(_all(db, CfLocation), key=lambda r: (r.sort_order, r.id))
    return [_cf_dict(r, groups) for r in rows]


def save_cf_location(db: Session, row_id: int | None, data: dict) -> dict:
    name = require_name(data.get("name"), "CF location name")
    try:
        payable = float(data.get("payable_pct", 100))
        reserve_raw = data.get("reserve_pct")
        reserve = None if reserve_raw in (None, "") else float(reserve_raw)
        sort_order = int(data.get("sort_order") or 0)
    except (TypeError, ValueError) as exc:
        raise MappingError("Order and percentages must be numbers.") from exc
    for label, value in (("Payable %", payable), ("Reserve %", reserve)):
        if value is not None and not 0 <= value <= 100:
            raise MappingError(f"{label} must be between 0 and 100.")
    group_name = clean_name(data.get("group") or "")
    group_id = _resolve(db, SummaryGroup, group_name, "Group", create=True) if group_name else None
    row = _upsert_named(db, CfLocation, row_id, name, "CF location", {
        "sort_order": sort_order, "show_in_summary": bool(data.get("show_in_summary", True)),
        "authorized_person": clean_name(data.get("authorized_person") or ""),
        "payable_pct": payable, "reserve_pct": reserve, "group_id": group_id,
    })
    return _cf_dict(row, {g.id: g.name for g in _all(db, SummaryGroup)})


def archive_cf_location(db: Session, row_id: int) -> None:
    used = (
        any(l.cf_location_id == row_id for l in _all(db, Location))
        or any(p.cf_location_id == row_id for p in _all(db, Party))
        or any(l.cf_location_id == row_id for l in _all(db, SharingRatioLine))
    )
    if used:
        raise MappingError("This CF location is still used by a location, party or sharing ratio.")
    _archive(db, CfLocation, row_id, "CF location")


# ── locations ───────────────────────────────────────────────────────────────

def list_locations(db: Session) -> list[dict]:
    cf = {c.id: c.name for c in _all(db, CfLocation)}
    return [
        {"id": r.id, "name": r.name, "cf_location": cf.get(r.cf_location_id, "")}
        for r in sorted(_all(db, Location), key=lambda r: r.name.casefold())
    ]


def save_location(db: Session, row_id: int | None, name: str, cf_location: str | None) -> dict:
    cf_id = _resolve(db, CfLocation, cf_location, "CF location", create=False)
    row = _upsert_named(db, Location, row_id, require_name(name, "Location name"), "Location",
                        {"cf_location_id": cf_id})
    return {"id": row.id, "name": row.name, "cf_location": clean_name(cf_location or "")}


def archive_location(db: Session, row_id: int) -> None:
    used = (
        any(p.location_id == row_id for p in _all(db, Party))
        or any(l.location_id == row_id for l in _all(db, SharingRatioLine))
    )
    if used:
        raise MappingError("This location is still used by a party or sharing ratio.")
    _archive(db, Location, row_id, "Location")


# ── sharing groups and ratio sets ───────────────────────────────────────────

def list_sharing_groups(db: Session) -> list[dict]:
    return [{"id": r.id, "name": r.name} for r in sorted(_all(db, SharingGroup), key=lambda r: r.name.casefold())]


def save_sharing_group(db: Session, row_id: int | None, name: str) -> dict:
    row = _upsert_named(db, SharingGroup, row_id, require_name(name, "Sharing group name"), "Sharing group", {})
    return {"id": row.id, "name": row.name}


def archive_sharing_group(db: Session, row_id: int) -> None:
    if any(p.sharing_group_id == row_id for p in _all(db, Party)):
        raise MappingError("This sharing group is still assigned to a party.")
    _archive(db, SharingGroup, row_id, "Sharing group")
    for ratio_set in _all(db, SharingRatioSet):
        if ratio_set.group_id == row_id:
            ratio_set.is_deleted = True


def list_ratio_sets(db: Session) -> list[dict]:
    locations = {l.id: l.name for l in _all(db, Location)}
    cf = {c.id: c.name for c in _all(db, CfLocation)}
    groups = {g.id: g.name for g in _all(db, SharingGroup)}
    lines: dict[int, list] = {}
    for line in sorted(_all(db, SharingRatioLine), key=lambda l: (l.sort_order, l.id)):
        lines.setdefault(line.set_id, []).append({
            "location": locations.get(line.location_id, ""), "ratio": line.ratio,
            "cf_location": cf.get(line.cf_location_id, ""),
        })
    sets = [s for s in _all(db, SharingRatioSet) if s.group_id in groups]
    sets.sort(key=lambda s: (groups[s.group_id].casefold(), s.effective_from))
    return [
        {"id": s.id, "group": groups[s.group_id], "effective_from": s.effective_from.isoformat(timespec="minutes"),
         "lines": lines.get(s.id, []), "total": sum(l["ratio"] for l in lines.get(s.id, []))}
        for s in sets
    ]


def _parse_effective(value: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise MappingError("Effective-from must be a date and time.") from exc


def save_ratio_set(db: Session, row_id: int | None, group: str, effective_from: str, lines: list[dict]) -> dict:
    group_id = _resolve(db, SharingGroup, group, "Sharing group", create=True)
    if group_id is None:
        raise MappingError("Sharing group is required.")
    when = _parse_effective(effective_from)
    if not lines:
        raise MappingError("Add at least one destination.")
    resolved: list[tuple[int, int | None, float]] = []
    seen: set[int] = set()
    for line in lines:
        # A line is just a particular (the label on the Working sheet) and
        # its ratio; the particular is stored as a Location row so a split
        # shows it as its location. When no CF location is given and the
        # particular is itself a CF location ("Main Operation"), that is it.
        location_id = _resolve(db, Location, line.get("location"), "Particular", create=True)
        if location_id is None:
            raise MappingError("Every line needs a particular.")
        if location_id in seen:
            raise MappingError(f"'{line.get('location')}' appears twice in this ratio set.")
        seen.add(location_id)
        cf_name = line.get("cf_location") or (
            line.get("location") if find_named(db, CfLocation, line.get("location") or "") else None)
        cf_id = _resolve(db, CfLocation, cf_name, "CF location")
        try:
            ratio = float(line.get("ratio"))
        except (TypeError, ValueError) as exc:
            raise MappingError("Every ratio must be a number.") from exc
        if ratio < 0:
            raise MappingError("Ratios cannot be negative.")
        resolved.append((location_id, cf_id, ratio))
    if abs(sum(r for _, _, r in resolved) - 1.0) > 1e-6:
        total = sum(r for _, _, r in resolved)
        raise MappingError(f"Ratios must add up to 100% (they add up to {total * 100:.4f}%).")

    existing = (
        db.query(SharingRatioSet)
        .filter(SharingRatioSet.group_id == group_id, SharingRatioSet.effective_from == when)
        .first()
    )
    if row_id is not None:
        ratio_set = db.get(SharingRatioSet, row_id)
        if ratio_set is None or ratio_set.is_deleted:
            raise MappingError("Ratio set not found.")
        if existing is not None and existing.id != ratio_set.id:
            raise MappingError("Another ratio set already starts at that date and time.")
        ratio_set.group_id, ratio_set.effective_from = group_id, when
    else:
        ratio_set = existing or SharingRatioSet(group_id=group_id, effective_from=when)
        if existing is None:
            db.add(ratio_set)
        ratio_set.is_deleted = False
    db.flush()

    old = {l.location_id: l for l in db.query(SharingRatioLine).filter(SharingRatioLine.set_id == ratio_set.id)}
    for index, (location_id, cf_id, ratio) in enumerate(resolved):
        line = old.pop(location_id, None) or SharingRatioLine(set_id=ratio_set.id, location_id=location_id)
        if line.id is None:
            db.add(line)
        line.cf_location_id, line.ratio, line.sort_order, line.is_deleted = cf_id, ratio, index, False
    for leftover in old.values():
        leftover.is_deleted = True
    db.flush()
    return {"id": ratio_set.id}


def archive_ratio_set(db: Session, row_id: int) -> None:
    _archive(db, SharingRatioSet, row_id, "Ratio set")


# ── parties ─────────────────────────────────────────────────────────────────

def list_parties(db: Session) -> list[dict]:
    locations = {l.id: l.name for l in _all(db, Location)}
    cf = {c.id: c.name for c in _all(db, CfLocation)}
    groups = {g.id: g.name for g in _all(db, SharingGroup)}
    alias_counts: dict[int, int] = {}
    for alias in _all(db, DescriptionAlias):
        alias_counts[alias.party_id] = alias_counts.get(alias.party_id, 0) + 1
    default_cf = {l.id: cf.get(l.cf_location_id, "") for l in _all(db, Location)}
    return [
        {
            "id": p.id, "name": p.name, "location": locations.get(p.location_id, ""),
            "cf_location": cf.get(p.cf_location_id, ""),
            "effective_cf_location": cf.get(p.cf_location_id) or default_cf.get(p.location_id, ""),
            "sharing_group": groups.get(p.sharing_group_id, ""),
            "alias_count": alias_counts.get(p.id, 0),
        }
        for p in sorted(_all(db, Party), key=lambda p: p.name.casefold())
    ]


def save_party(db: Session, row_id: int | None, name: str, location: str | None,
               cf_location: str | None, sharing_group: str | None) -> dict:
    name = require_name(name, "Party name")
    group_id = _resolve(db, SharingGroup, sharing_group, "Sharing group")
    location_id = _resolve(db, Location, location, "Location", create=True)
    cf_id = _resolve(db, CfLocation, cf_location, "CF location")
    if group_id is not None:
        location_id = cf_id = None  # the sharing ratios decide where the money goes
    row = _upsert_named(db, Party, row_id, name, "Party", {
        "location_id": location_id, "cf_location_id": cf_id, "sharing_group_id": group_id,
    })
    return {"id": row.id}


def archive_party(db: Session, row_id: int) -> None:
    if any(a.party_id == row_id for a in _all(db, DescriptionAlias)):
        raise MappingError("Remove or re-point this party's description aliases first.")
    _archive(db, Party, row_id, "Party")


def get_or_create_party(db: Session, name: str, location: str | None = None) -> Party:
    """Party for the fix-up panel: reuse an existing one, else create it
    (with `location` if given). An existing party's location is only filled
    in when it has none - the fix panel never overwrites a mapping."""
    name = require_name(name, "Party name")
    row = find_named(db, Party, name)
    location_id = _resolve(db, Location, location, "Location") if location else None
    if row is None:
        row = Party(name=name, location_id=location_id)
        db.add(row)
    else:
        row.is_deleted = False
        if row.location_id is None and row.sharing_group_id is None and location_id is not None:
            row.location_id = location_id
    db.flush()
    return row


# ── description aliases ─────────────────────────────────────────────────────

def list_aliases(db: Session) -> list[dict]:
    parties = {p.id: p.name for p in _all(db, Party)}
    return [
        {"id": a.id, "desc_key": a.desc_key, "party": parties.get(a.party_id, "")}
        for a in sorted(_all(db, DescriptionAlias), key=lambda a: a.desc_key)
        if a.party_id in parties
    ]


def save_alias(db: Session, row_id: int | None, desc_key: str, party: str) -> dict:
    key = description_key(require_name(desc_key, "Description key"))
    if not key:
        raise MappingError("Description key is empty after removing numbers and codes.")
    if re.fullmatch(r"[\d\s/:.-]+", key):
        raise MappingError("That looks like a date or time, not a bank description.")
    party_id = _resolve(db, Party, party, "Party")
    if party_id is None:
        raise MappingError("Party is required.")
    existing = db.query(DescriptionAlias).filter(DescriptionAlias.desc_key == key).first()
    if row_id is not None:
        row = db.get(DescriptionAlias, row_id)
        if row is None or row.is_deleted:
            raise MappingError("Alias not found.")
        if existing is not None and existing.id != row.id:
            raise MappingError("That description key is already mapped.")
        row.desc_key = key
    else:
        row = existing or DescriptionAlias(desc_key=key, party_id=party_id)
        if existing is None:
            db.add(row)
    row.party_id, row.is_deleted = party_id, False
    db.flush()
    return {"id": row.id, "desc_key": key}


def archive_alias(db: Session, row_id: int) -> None:
    _archive(db, DescriptionAlias, row_id, "Alias")


def names_for_suggestions(db: Session) -> dict[str, list[str]]:
    return {
        "cf_locations": sorted((c.name for c in _all(db, CfLocation)), key=str.casefold),
        "locations": sorted((l.name for l in _all(db, Location)), key=str.casefold),
        "parties": sorted((p.name for p in _all(db, Party)), key=str.casefold),
        "sharing_groups": sorted((g.name for g in _all(db, SharingGroup)), key=str.casefold),
        "summary_groups": sorted((g.name for g in _all(db, SummaryGroup)), key=str.casefold),
    }
