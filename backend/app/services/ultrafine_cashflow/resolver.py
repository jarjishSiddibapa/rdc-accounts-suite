"""Resolve bank transactions to cash-flow allocation rows.

Nothing here is stored. A transaction's name, location and CF location are
recomputed from the mappings every time, so a single mapping edit is
reflected in every past and future month with no copies to keep in sync.

Resolution order for one transaction:

  1. Manual splits typed against the transaction (e.g. a "Multiple Payees"
     batch debit) are used as they are.
  2. Otherwise the party is the transaction's own override or the party its
     normalized description key points to.
  3. A party in a sharing group is split by the ratio set in force on the
     transaction's date; the last line absorbs rounding so the pieces always
     add up to the exact bank amount.
  4. Any other party goes to its location, and the CF location is the
     party's override or the location's default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from .keys import casefold_key, description_key
from .models import (
    CfLocation,
    DescriptionAlias,
    Location,
    Party,
    SharingGroup,
    SharingRatioLine,
    SharingRatioSet,
    Transaction,
    TransactionSplit,
)

CENT = Decimal("0.01")

ISSUE_NO_PARTY = "no_party"
ISSUE_NO_LOCATION = "no_location"
ISSUE_NO_CF = "no_cf_location"
ISSUE_NO_RATIO = "no_ratio"
ISSUE_SPLIT_MISMATCH = "split_mismatch"

# Plain wording of each issue, for people reading a workbook written "anyway".
ISSUE_TEXT = {
    ISSUE_NO_PARTY: "Description not mapped to a name",
    ISSUE_NO_LOCATION: "Name has no location",
    ISSUE_NO_CF: "Location has no CF location",
    ISSUE_NO_RATIO: "No sharing ratio in force on this date",
    ISSUE_SPLIT_MISMATCH: "Typed split does not add up to the bank amount",
}


@dataclass
class Maps:
    cf_locations: dict[int, CfLocation] = field(default_factory=dict)
    cf_by_name: dict[str, CfLocation] = field(default_factory=dict)
    locations: dict[int, Location] = field(default_factory=dict)
    parties: dict[int, Party] = field(default_factory=dict)
    aliases: dict[str, int] = field(default_factory=dict)
    groups: dict[int, SharingGroup] = field(default_factory=dict)
    # group_id -> [(effective_from, [lines])] ascending by effective_from
    ratio_sets: dict[int, list[tuple[datetime, list[SharingRatioLine]]]] = field(default_factory=dict)

    def cf_for(self, location_id: int | None, override_id: int | None = None) -> CfLocation | None:
        if override_id is not None:
            cf = self.cf_locations.get(override_id)
            if cf is not None:
                return cf
        if location_id is None:
            return None
        location = self.locations.get(location_id)
        if location is None:
            return None
        if location.cf_location_id is not None:
            return self.cf_locations.get(location.cf_location_id)
        # No default set: a location named like a CF location (Main Operation,
        # Flyash Business, ...) belongs to that CF location.
        return self.cf_by_name.get(casefold_key(location.name))


def load_maps(db: Session) -> Maps:
    active = lambda model: db.query(model).filter(model.is_deleted == False)  # noqa: E712
    maps = Maps()
    maps.cf_locations = {r.id: r for r in active(CfLocation)}
    maps.cf_by_name = {casefold_key(r.name): r for r in maps.cf_locations.values()}
    maps.locations = {r.id: r for r in active(Location)}
    maps.parties = {r.id: r for r in active(Party)}
    maps.groups = {r.id: r for r in active(SharingGroup)}
    maps.aliases = {r.desc_key: r.party_id for r in active(DescriptionAlias) if r.party_id in maps.parties}

    lines_by_set: dict[int, list[SharingRatioLine]] = {}
    for line in active(SharingRatioLine).order_by(SharingRatioLine.sort_order, SharingRatioLine.id):
        lines_by_set.setdefault(line.set_id, []).append(line)
    for ratio_set in active(SharingRatioSet).order_by(SharingRatioSet.effective_from):
        if ratio_set.group_id in maps.groups:
            maps.ratio_sets.setdefault(ratio_set.group_id, []).append(
                (ratio_set.effective_from, lines_by_set.get(ratio_set.id, []))
            )
    return maps


@dataclass
class Allocation:
    txn_id: int
    txn_dt: datetime
    direction: str
    description: str
    reference_no: str
    txn_amount: Decimal
    name: str
    amount: Decimal
    location: str
    cf_location: str
    issues: list[str] = field(default_factory=list)


def party_for(txn: Transaction, maps: Maps) -> Party | None:
    if txn.party_id is not None and txn.party_id in maps.parties:
        return maps.parties[txn.party_id]
    party_id = maps.aliases.get(description_key(txn.description))
    return maps.parties.get(party_id) if party_id is not None else None


def ratio_lines_for(maps: Maps, group_id: int, when: datetime):
    """The ratio lines in force at `when`, or None when none is."""
    chosen = None
    for effective_from, lines in maps.ratio_sets.get(group_id, []):
        if effective_from <= when:
            chosen = lines
        else:
            break
    return chosen


def _split_evenly(total: Decimal, ratios: list[float]) -> list[Decimal]:
    """Round each share to paise; the last one takes the remainder."""
    shares = [(total * Decimal(repr(r))).quantize(CENT, rounding=ROUND_HALF_UP) for r in ratios]
    if shares:
        shares[-1] = total - sum(shares[:-1], Decimal("0"))
    return shares


def allocate(txn: Transaction, maps: Maps, splits: list[TransactionSplit]) -> list[Allocation]:
    def make(name: str, amount: Decimal, location: str, cf: str, *issues: str) -> Allocation:
        return Allocation(
            txn_id=txn.id, txn_dt=txn.txn_dt, direction=txn.direction,
            description=txn.description, reference_no=txn.reference_no or "",
            txn_amount=Decimal(txn.amount), name=name, amount=amount,
            location=location, cf_location=cf, issues=list(issues),
        )

    party = party_for(txn, maps)
    name = party.name if party else ""
    total = Decimal(txn.amount)

    if splits:
        rows: list[Allocation] = []
        for split in splits:
            location = maps.locations.get(split.location_id)
            cf = maps.cf_for(split.location_id, split.cf_location_id)
            issues = []
            if location is None:
                issues.append(ISSUE_NO_LOCATION)
            elif cf is None:
                issues.append(ISSUE_NO_CF)
            rows.append(make(name, Decimal(split.amount), location.name if location else "",
                             cf.name if cf else "", *issues))
        if sum((r.amount for r in rows), Decimal("0")) != total:
            for row in rows:
                row.issues.append(ISSUE_SPLIT_MISMATCH)
        return rows

    if party is None:
        return [make("", total, "", "", ISSUE_NO_PARTY)]

    if party.sharing_group_id is not None and party.sharing_group_id in maps.groups:
        lines = ratio_lines_for(maps, party.sharing_group_id, txn.txn_dt)
        if not lines:
            return [make(name, total, "", "", ISSUE_NO_RATIO)]
        rows = []
        for line, share in zip(lines, _split_evenly(total, [l.ratio for l in lines])):
            location = maps.locations.get(line.location_id)
            cf = maps.cf_for(line.location_id, line.cf_location_id)
            issues = []
            if location is None:
                issues.append(ISSUE_NO_LOCATION)
            elif cf is None:
                issues.append(ISSUE_NO_CF)
            rows.append(make(name, share, location.name if location else "",
                             cf.name if cf else "", *issues))
        return rows

    location = maps.locations.get(party.location_id) if party.location_id else None
    if location is None:
        return [make(name, total, "", "", ISSUE_NO_LOCATION)]
    cf = maps.cf_for(party.location_id, party.cf_location_id)
    if cf is None:
        return [make(name, total, location.name, "", ISSUE_NO_CF)]
    return [make(name, total, location.name, cf.name)]


def load_splits(db: Session, txn_ids: list[int]) -> dict[int, list[TransactionSplit]]:
    result: dict[int, list[TransactionSplit]] = {}
    if not txn_ids:
        return result
    for chunk_start in range(0, len(txn_ids), 500):
        chunk = txn_ids[chunk_start:chunk_start + 500]
        for split in (
            db.query(TransactionSplit)
            .filter(TransactionSplit.txn_id.in_(chunk), TransactionSplit.is_deleted == False)  # noqa: E712
            .order_by(TransactionSplit.sort_order, TransactionSplit.id)
        ):
            result.setdefault(split.txn_id, []).append(split)
    return result


def month_bounds(month: str) -> tuple[datetime, datetime]:
    year, mon = int(month[:4]), int(month[5:7])
    start = datetime(year, mon, 1)
    end = datetime(year + (mon == 12), (mon % 12) + 1, 1)
    return start, end


def transactions_in_month(db: Session, month: str) -> list[Transaction]:
    start, end = month_bounds(month)
    return (
        db.query(Transaction)
        .filter(Transaction.is_deleted == False, Transaction.txn_dt >= start, Transaction.txn_dt < end)  # noqa: E712
        .order_by(Transaction.txn_dt, Transaction.id)
        .all()
    )


def allocations_for_month(db: Session, month: str, maps: Maps | None = None) -> list[Allocation]:
    maps = maps or load_maps(db)
    txns = transactions_in_month(db, month)
    splits = load_splits(db, [t.id for t in txns])
    rows: list[Allocation] = []
    for txn in txns:
        rows.extend(allocate(txn, maps, splits.get(txn.id, [])))
    return rows


def unmapped_summary(allocations: list[Allocation], maps: Maps) -> dict:
    """Group the unresolved allocations into what a person has to fix:
    description keys without a party, parties without a location, etc."""
    no_party: dict[str, dict] = {}
    other: dict[tuple[str, str], dict] = {}
    for row in allocations:
        for issue in row.issues:
            if issue == ISSUE_NO_PARTY:
                key = description_key(row.description)
                entry = no_party.setdefault(
                    key, {"desc_key": key, "sample": row.description, "direction": row.direction,
                          "count": 0, "total": Decimal("0")},
                )
                entry["count"] += 1
                entry["total"] += row.amount
            else:
                entry = other.setdefault(
                    (issue, row.name or row.description[:60]),
                    {"issue": issue, "name": row.name, "sample": row.description[:80], "count": 0},
                )
                entry["count"] += 1
    return {
        "no_party": [
            {**v, "total": float(v["total"])}
            for v in sorted(no_party.values(), key=lambda v: -v["total"])
        ],
        "other": sorted(other.values(), key=lambda v: (v["issue"], v["name"])),
    }
