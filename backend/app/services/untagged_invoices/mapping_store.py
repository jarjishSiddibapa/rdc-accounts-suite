"""Persistence layer for the Untagged Invoices Report tool's 2 mapping
tables, backed by the suite's shared MySQL database (see models.py).

Seeded on first use from app.services.unapplied_receipts.mapping_store's
LOCATION_MAP / ACCOUNT_INCHARGE_MAP constants - this tool's input (an
Oracle AR aging export) carries the exact same raw "Location Name" text as
that tool's ageing file, so starting from identical data means this new
report matches on day one. The two tools' tables are otherwise independent
from that point on (see models.py's docstring for why).

Internal in-memory formats
────────────────────────────────────────────────────────────────────────────
location_map  : { raw_location_name_upper: mapped_location }
incharge_map  : { location: accounts_incharge }
"""

from sqlalchemy.orm import Session

from app.services.unapplied_receipts.mapping_store import (
    ACCOUNT_INCHARGE_MAP as _SEED_INCHARGE_MAP,
    LOCATION_MAP as _SEED_LOCATION_MAP,
)
from app.soft_delete import delete_keyed_row, upsert_keyed_row

from .models import AccountsInchargeMap, LocationMap


def _maybe_seed(db: Session) -> None:
    """First-run only: if a mapping table is completely empty, seed it from
    the unapplied_receipts defaults so this report doesn't launch with zero
    mapped locations."""
    if db.query(LocationMap.id).first() is None:
        for raw_location, location in _SEED_LOCATION_MAP.items():
            db.add(LocationMap(location_name=raw_location, location=location))
        db.commit()

    if db.query(AccountsInchargeMap.id).first() is None:
        for location, incharge in _SEED_INCHARGE_MAP.items():
            db.add(AccountsInchargeMap(location=location, accounts_incharge=incharge))
        db.commit()


def load_all(db: Session) -> tuple[dict[str, str], dict[str, str]]:
    """Return (location_map, incharge_map) read from the shared MySQL
    tables. Seeds from the hardcoded defaults on first run if empty."""
    _maybe_seed(db)

    location_map: dict[str, str] = {}
    for row in db.query(LocationMap).filter(LocationMap.is_deleted == False).all():  # noqa: E712
        location_map[row.location_name] = row.location or ""

    incharge_map: dict[str, str] = {}
    for row in db.query(AccountsInchargeMap).filter(AccountsInchargeMap.is_deleted == False).all():  # noqa: E712
        incharge_map[row.location] = row.accounts_incharge or ""

    return location_map, incharge_map


# ── Single-row CRUD (safe under concurrent edits — see app/soft_delete.py) ──

def upsert_location(db: Session, location_name: str, location: str) -> None:
    upsert_keyed_row(db, LocationMap, ("location_name",), location_name.strip().upper(), {
        "location": location.strip(),
    })
    db.commit()


def delete_location(db: Session, location_name: str) -> bool:
    result = delete_keyed_row(db, LocationMap, ("location_name",), location_name.strip().upper())
    db.commit()
    return result


def upsert_accounts_incharge(db: Session, location: str, accounts_incharge: str) -> None:
    upsert_keyed_row(db, AccountsInchargeMap, ("location",), location.strip(), {
        "accounts_incharge": accounts_incharge.strip(),
    })
    db.commit()


def delete_accounts_incharge(db: Session, location: str) -> bool:
    result = delete_keyed_row(db, AccountsInchargeMap, ("location",), location.strip())
    db.commit()
    return result
