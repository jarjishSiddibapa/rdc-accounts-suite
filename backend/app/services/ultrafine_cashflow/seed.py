"""Additive startup seed for the Ultrafine Cashflow mappings.

seed_data/ultrafine-cashflow-seed.json was distilled from the team's manual
"UF Cashflow" workbooks (see seed_data/build_ultrafine_cashflow_seed.py).
Like every other seed in the suite it only adds natural keys that have never
existed: an existing or archived row is never overwritten or revived, so
administrator edits always win.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app import config

from .generate import attach_workbook
from .keys import casefold_key, clean_name, description_key
from .models import (
    CashflowFile,
    CfLocation,
    DescriptionAlias,
    Location,
    Party,
    PlanItem,
    PlanSection,
    SharingGroup,
    SharingRatioLine,
    SharingRatioSet,
    SummaryGroup,
)

SEED_FILE = config.SEED_DIR / "ultrafine-cashflow-seed.json"
# The team's own October cashflow, the latest and most reliable one: offered for download until
# statements are uploaded and the month is generated (which replaces it).
OCTOBER_WORKBOOK = (config.SEED_DIR / "ultrafine-cashflow-oct-2026.xlsx", "2026-10")


def _index(db: Session, model) -> dict[str, object]:
    return {casefold_key(row.name): row for row in db.query(model).all()}


def seed_missing_from_json(db: Session) -> dict[str, int]:
    if not SEED_FILE.is_file():
        return {}
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    added: dict[str, int] = {}

    def bump(kind: str) -> None:
        added[kind] = added.get(kind, 0) + 1

    groups = _index(db, SummaryGroup)
    for name in data.get("summary_groups", []):
        if casefold_key(name) not in groups:
            groups[casefold_key(name)] = row = SummaryGroup(name=clean_name(name))
            db.add(row)
            bump("summary_groups")
    db.flush()

    cf_locations = _index(db, CfLocation)
    for item in data.get("cf_locations", []):
        if casefold_key(item["name"]) in cf_locations:
            continue
        group = groups.get(casefold_key(item["group"])) if item.get("group") else None
        row = CfLocation(
            name=clean_name(item["name"]), sort_order=item.get("sort_order", 0),
            show_in_summary=item.get("show_in_summary", True),
            authorized_person=item.get("authorized_person", ""),
            payable_pct=item.get("payable_pct", 100), reserve_pct=item.get("reserve_pct"),
            group_id=group.id if group else None,
        )
        db.add(row)
        cf_locations[casefold_key(row.name)] = row
        bump("cf_locations")
    db.flush()

    locations = _index(db, Location)
    for item in data.get("locations", []):
        if casefold_key(item["name"]) in locations:
            continue
        cf = cf_locations.get(casefold_key(item.get("cf_location") or ""))
        row = Location(name=clean_name(item["name"]), cf_location_id=cf.id if cf else None)
        db.add(row)
        locations[casefold_key(row.name)] = row
        bump("locations")
    db.flush()

    sharing_groups = _index(db, SharingGroup)
    for name in data.get("sharing_groups", []):
        if casefold_key(name) not in sharing_groups:
            sharing_groups[casefold_key(name)] = row = SharingGroup(name=clean_name(name))
            db.add(row)
            bump("sharing_groups")
    db.flush()

    parties = _index(db, Party)
    for item in data.get("parties", []):
        if casefold_key(item["name"]) in parties:
            continue
        location = locations.get(casefold_key(item.get("location") or ""))
        cf = cf_locations.get(casefold_key(item.get("cf_location") or ""))
        group = sharing_groups.get(casefold_key(item.get("sharing_group") or ""))
        row = Party(
            name=clean_name(item["name"]),
            location_id=location.id if location else None,
            cf_location_id=cf.id if cf else None,
            sharing_group_id=group.id if group else None,
        )
        db.add(row)
        parties[casefold_key(row.name)] = row
        bump("parties")
    db.flush()

    known_keys = {a.desc_key for a in db.query(DescriptionAlias).all()}
    for item in data.get("aliases", []):
        key = description_key(item["desc_key"])
        party = parties.get(casefold_key(item["party"]))
        if not key or key in known_keys or party is None:
            continue
        db.add(DescriptionAlias(desc_key=key, party_id=party.id))
        known_keys.add(key)
        bump("aliases")
    db.flush()

    for group_name, sets in data.get("ratio_sets", {}).items():
        group = sharing_groups.get(casefold_key(group_name))
        if group is None:
            continue
        existing = {s.effective_from for s in db.query(SharingRatioSet).filter(SharingRatioSet.group_id == group.id)}
        for entry in sets:
            effective = datetime.fromisoformat(entry["effective_from"])
            if effective in existing:
                continue
            ratio_set = SharingRatioSet(group_id=group.id, effective_from=effective)
            db.add(ratio_set)
            db.flush()
            for order, line in enumerate(entry["lines"]):
                location = locations.get(casefold_key(line["location"]))
                if location is None:
                    continue
                cf = cf_locations.get(casefold_key(line.get("cf_location") or ""))
                db.add(SharingRatioLine(
                    set_id=ratio_set.id, location_id=location.id,
                    cf_location_id=cf.id if cf else None, ratio=line["ratio"], sort_order=order,
                ))
            existing.add(effective)
            bump("ratio_sets")
    db.flush()

    plan = data.get("plan") or {}
    month = plan.get("month")
    if month and not db.query(PlanSection).filter(PlanSection.month == month).first():
        for order, section in enumerate(plan.get("sections", [])):
            row = PlanSection(month=month, block_no=section.get("block_no", 1),
                              title=section.get("title", ""), sort_order=order)
            db.add(row)
            db.flush()
            for item_order, item in enumerate(section.get("items", [])):
                db.add(PlanItem(section_id=row.id, label=item["label"],
                                amount_expr=item.get("amount_expr", ""), sort_order=item_order))
            bump("plan_sections")

    workbook, month = OCTOBER_WORKBOOK
    if workbook.is_file() and not db.query(CashflowFile).filter(CashflowFile.month == month).first():
        attach_workbook(db, month, workbook, None)
        bump("cashflow_files")
    return added
