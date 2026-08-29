"""Persistence for the Ledger Nature fallback table (see models.py)."""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import SEED_DIR
from app.soft_delete import delete_keyed_row, restore_keyed_row, seed_missing_keyed_rows

from .models import LedgerNature

SEED_PATH = SEED_DIR / "trial-balance-formatter-ledger-natures.json"


class ArchivedMappingError(ValueError):
    """The requested normalized ledger key belongs to an archived row."""


class DuplicateMappingError(ValueError):
    """A different active row already owns the requested normalized key."""


def _s(value) -> str:
    return str(value).strip() if value is not None else ""


def ledger_key(value: str) -> str:
    return _s(value).upper()


def load_all(db: Session) -> dict[str, dict]:
    """Return authoritative classifications for every active ledger."""
    rows = db.query(LedgerNature).filter(LedgerNature.is_deleted.is_(False)).all()
    return {
        row.ledger_key: {
            "nature": row.nature,
            "is_subgroup": bool(row.is_subgroup),
        }
        for row in rows
    }


def list_rows(db: Session, *, archived: bool = False) -> list[dict]:
    rows = (
        db.query(LedgerNature)
        .filter(LedgerNature.is_deleted.is_(archived))
        .order_by(LedgerNature.ledger_name)
        .all()
    )
    return [
        {
            "ledger_name": row.ledger_name,
            "nature": row.nature,
            "is_subgroup": bool(row.is_subgroup),
        }
        for row in rows
    ]


def set_nature(
    db: Session,
    ledger_name: str,
    nature: str,
    is_subgroup: bool = False,
    *,
    original_name: str | None = None,
) -> None:
    name = _s(ledger_name)
    if not name:
        raise ValueError("Ledger name is required")
    if nature not in ("Dr", "Cr"):
        raise ValueError("Nature must be 'Dr' or 'Cr'")
    new_key = ledger_key(name)
    old_key = ledger_key(original_name) if original_name is not None else None
    target = db.query(LedgerNature).filter(LedgerNature.ledger_key == new_key).first()

    if old_key is None:
        if target is not None and target.is_deleted:
            raise ArchivedMappingError("This ledger exists in the archive. Restore it instead of adding a duplicate.")
    elif new_key != old_key:
        original = db.query(LedgerNature).filter(
            LedgerNature.ledger_key == old_key,
            LedgerNature.is_deleted.is_(False),
        ).first()
        if original is None:
            raise LookupError("Mapping not found")
        if target is not None:
            if target.is_deleted:
                raise ArchivedMappingError("The new ledger name exists in the archive. Restore that row first.")
            raise DuplicateMappingError("A mapping already exists for the new ledger name.")
        original.is_deleted = True
        target = None

    if target is None:
        target = LedgerNature(ledger_key=new_key)
        db.add(target)
    target.ledger_name = name
    target.nature = nature
    target.is_subgroup = bool(is_subgroup)
    target.is_deleted = False
    db.commit()


def seed_missing_from_json(db: Session, path: "str | Path" = SEED_PATH) -> int:
    """Pre-populate every classification verified in the supplied reference.

    This is purely additive: an administrator's edit or archive decision is
    never overwritten, and a new ledger still surfaces for one-time review.
    """
    source = Path(path)
    if not source.is_file():
        return 0
    data = json.loads(source.read_text(encoding="utf-8"))
    seed = {
        ledger_key(name): {
            "ledger_name": name,
            "nature": values["nature"] if isinstance(values, dict) else values,
            "is_subgroup": bool(values.get("is_subgroup", False)) if isinstance(values, dict) else False,
        }
        for name, values in data.items()
    }
    inserted = seed_missing_keyed_rows(db, LedgerNature, ("ledger_key",), seed)
    db.commit()
    return inserted


def archive_nature(db: Session, ledger_name: str) -> bool:
    changed = delete_keyed_row(db, LedgerNature, ("ledger_key",), ledger_key(ledger_name))
    db.commit()
    return changed


def restore_nature(db: Session, ledger_name: str) -> bool:
    changed = restore_keyed_row(db, LedgerNature, ("ledger_key",), ledger_key(ledger_name))
    db.commit()
    return changed
