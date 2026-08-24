"""Persistence layer for the Ultrafine Balance Confirmation tool's
Customer -> Email mapping (see models.py's CustomerEmailMap).

Starts empty — there is no seed data, matching the original desktop app's
own design where every customer's recipients were re-typed into the input
Excel on each run (see processing.py's module docstring). The suite now
remembers whatever an admin/user saves here across runs, while a fresh
upload can still add to or override it for one particular send (see
processing.merge_with_mapping).

In-memory shape (mirrors the sibling mapping stores' "dict of all current
rows" pattern used by app/soft_delete.py's sync_keyed_rows):
    { customer_name: {"to": [str, ...], "cc": [str, ...]} }
"""

from sqlalchemy.orm import Session

from app.soft_delete import delete_keyed_row, sync_keyed_rows, upsert_keyed_row

from .models import CustomerEmailMap


def _split(value) -> list[str]:
    raw = "" if value is None else str(value)
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_all(db: Session) -> dict[str, dict]:
    """Return {customer_name: {"to": [...], "cc": [...]}} for every active
    (non-soft-deleted) row."""
    rows = (
        db.query(CustomerEmailMap)
        .filter(CustomerEmailMap.is_deleted == False)  # noqa: E712
        .all()
    )
    return {
        row.customer_name: {
            "to": _split(row.to_emails),
            "cc": _split(row.cc_emails),
        }
        for row in rows
    }


def save_all(db: Session, mapping: dict[str, dict]) -> None:
    """Sync the table to match `mapping` (customer_name -> {"to": [...],
    "cc": [...]}), soft-delete-aware (see app/soft_delete.py): a customer
    missing from `mapping` gets is_deleted=True instead of being removed,
    and a customer whose key reappears (even if previously archived) is
    revived and updated in place. Does not commit — caller commits."""
    rows_by_key = {
        customer_name.strip(): {
            "to_emails": ", ".join(data.get("to", [])),
            "cc_emails": ", ".join(data.get("cc", [])),
        }
        for customer_name, data in mapping.items()
        if customer_name and customer_name.strip()
    }
    sync_keyed_rows(db, CustomerEmailMap, ("customer_name",), rows_by_key)


# ── Single-row CRUD (safe under concurrent edits — see app/soft_delete.py's
#    upsert_keyed_row/delete_keyed_row). save_all() above always rewrites the
#    whole table from one combined in-memory snapshot - adding/editing/
#    deleting a single customer used to go through load_all() -> mutate one
#    entry -> save_all(), which races: two such requests close together
#    (completely normal usage) can each read a snapshot that doesn't yet
#    include the other's write, and whichever save lands last silently
#    soft-deletes the other one's row. ─────────────────────────────────────

def upsert_customer_mapping(db: Session, customer_name: str, to_emails: list[str], cc_emails: list[str]) -> None:
    upsert_keyed_row(db, CustomerEmailMap, ("customer_name",), customer_name.strip(), {
        "to_emails": ", ".join(to_emails),
        "cc_emails": ", ".join(cc_emails),
    })
    db.commit()


def delete_customer_mapping(db: Session, customer_name: str) -> bool:
    result = delete_keyed_row(db, CustomerEmailMap, ("customer_name",), customer_name.strip())
    db.commit()
    return result
