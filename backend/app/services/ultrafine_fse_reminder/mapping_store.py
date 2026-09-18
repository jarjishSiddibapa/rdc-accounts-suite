"""Persistence layer for the Ultrafine FSE Bulk Reminder tool's
FSE -> Email mapping (see models.py's FseEmailMap).

Mirrors the sibling ultrafine_balance_confirmation/mapping_store.py's
shape exactly, adapted to a single email string per FSE instead of
separate to/cc lists.

In-memory shape: {fse_name: email}
"""

from sqlalchemy.orm import Session

from app.soft_delete import delete_keyed_row, sync_keyed_rows, upsert_keyed_row

from .models import FseEmailMap


def load_all(db: Session) -> dict[str, str]:
    """Return {fse_name: email} for every active (non-soft-deleted) row."""
    rows = (
        db.query(FseEmailMap)
        .filter(FseEmailMap.is_deleted == False)  # noqa: E712
        .all()
    )
    return {row.fse_name: row.email or "" for row in rows}


def save_all(db: Session, mapping: dict[str, str]) -> None:
    """Sync the table to match `mapping` (fse_name -> email), soft-delete-
    aware (see app/soft_delete.py). Does not commit - caller commits."""
    rows_by_key = {
        fse_name.strip(): {"email": (email or "").strip()}
        for fse_name, email in mapping.items()
        if fse_name and fse_name.strip()
    }
    sync_keyed_rows(db, FseEmailMap, ("fse_name",), rows_by_key)


# ── Single-row CRUD (safe under concurrent edits - see app/soft_delete.py's
#    upsert_keyed_row/delete_keyed_row - two saves close together can't
#    silently soft-delete each other's row the way a read-modify-write
#    save_all() round trip could). ─────────────────────────────────────────

def upsert_fse_mapping(db: Session, fse_name: str, email: str) -> None:
    upsert_keyed_row(db, FseEmailMap, ("fse_name",), fse_name.strip(), {"email": (email or "").strip()})
    db.commit()


def delete_fse_mapping(db: Session, fse_name: str) -> bool:
    result = delete_keyed_row(db, FseEmailMap, ("fse_name",), fse_name.strip())
    db.commit()
    return result
