"""Persistence for the Debtors Reports Auto Mailer's 2 mapping tables.

Seeded once, the first time they are read while empty, from the current
mappings of the Untagged Invoices and Unapplied Receipts tools (both already
on the ERP's region names, see app.services.customer_region) so the mailer
starts out producing what those tools produce. Edited here from then on.

Internal in-memory formats
────────────────────────────────────────────────────────────────────────────
location_map  : { raw_location_name_upper: location }
incharge_map  : { location: accounts_incharge }
"""

from sqlalchemy.orm import Session

from app.soft_delete import delete_keyed_row, upsert_keyed_row

from .models import AccountsInchargeMap, LocationMap


def _maybe_seed(db: Session) -> None:
    if db.query(LocationMap.id).first() is None:
        # imported here: these import the database module at load time
        from app.services.unapplied_receipts import mapping_store as unapplied
        from app.services.untagged_invoices import mapping_store as untagged

        untagged_locations, untagged_incharges = untagged.load_all(db)
        unapplied_incharges, unapplied_sites = unapplied.load_all(db)
        for name, location in {**unapplied_sites, **untagged_locations}.items():
            db.add(LocationMap(location_name=name.strip().upper(), location=location))
        for location, incharge in {**unapplied_incharges, **untagged_incharges}.items():
            db.add(AccountsInchargeMap(location=location, accounts_incharge=incharge))
        db.commit()


def load_all(db: Session) -> tuple[dict[str, str], dict[str, str]]:
    """(location_map, incharge_map) from the shared MySQL tables."""
    _maybe_seed(db)

    location_map = {
        row.location_name: row.location or ""
        for row in db.query(LocationMap).filter(LocationMap.is_deleted == False).all()  # noqa: E712
    }
    incharge_map = {
        row.location: row.accounts_incharge or ""
        for row in db.query(AccountsInchargeMap).filter(AccountsInchargeMap.is_deleted == False).all()  # noqa: E712
    }
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
