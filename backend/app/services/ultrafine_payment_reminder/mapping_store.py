"""Persistence layer for the Customer -> Email mapping table.

Replaces the desktop app's per-send "Emails" Excel upload with a row in the
suite's shared MySQL database. Unlike RDC Payables' mapping_store.py, this
table starts completely empty on a fresh database — there is no legacy
workbook to seed from; every mapping is created by an admin/user through the
mapping CRUD endpoints or discovered from an uploaded Emails Excel.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.soft_delete import delete_keyed_row, sync_keyed_rows, upsert_keyed_row

from .models import CustomerEmailMap


def _s(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def load_all(db: Session) -> dict[str, dict[str, str]]:
    """Return {customer_name: {"to_emails": str, "cc_emails": str}} for
    every active mapping row."""
    mapping: dict[str, dict[str, str]] = {}
    for row in db.query(CustomerEmailMap).filter(CustomerEmailMap.is_deleted == False).all():  # noqa: E712
        mapping[_s(row.customer_name)] = {
            "to_emails": _s(row.to_emails),
            "cc_emails": _s(row.cc_emails),
        }
    return mapping


def save_all(db: Session, mapping: dict[str, dict[str, str]]) -> None:
    """Sync the mapping table to match `mapping` exactly - soft-delete-aware
    (see app/soft_delete.py): a customer name missing from `mapping` gets
    archived (is_deleted=True), never hard-deleted; a name whose key
    reappears is revived and updated in place. Commits."""
    sync_keyed_rows(
        db,
        CustomerEmailMap,
        ("customer_name",),
        {
            customer_name: {
                "to_emails": entry.get("to_emails", ""),
                "cc_emails": entry.get("cc_emails", ""),
            }
            for customer_name, entry in mapping.items()
        },
    )
    db.commit()


# ── Single-row CRUD (safe under concurrent edits — see app/soft_delete.py's
#    upsert_keyed_row/delete_keyed_row). save_all() above always rewrites the
#    whole table from one combined in-memory snapshot - adding/editing/
#    deleting a single customer used to go through load_all() -> mutate one
#    entry -> save_all(), which races: two such requests close together
#    (completely normal usage) can each read a snapshot that doesn't yet
#    include the other's write, and whichever save lands last silently
#    soft-deletes the other one's row. ─────────────────────────────────────

def upsert_customer_mapping(db: Session, customer_name: str, to_emails: str, cc_emails: str) -> None:
    upsert_keyed_row(db, CustomerEmailMap, ("customer_name",), customer_name.strip(), {
        "to_emails": to_emails,
        "cc_emails": cc_emails,
    })
    db.commit()


def delete_customer_mapping(db: Session, customer_name: str) -> bool:
    result = delete_keyed_row(db, CustomerEmailMap, ("customer_name",), customer_name.strip())
    db.commit()
    return result
