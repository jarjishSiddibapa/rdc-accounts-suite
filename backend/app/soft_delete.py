"""Generic soft-delete-aware sync helper.

The whole application uses soft deletes: nothing is ever actually removed
from the database, every table has an `is_deleted` flag instead. The
mapping-table editors (RDC Payables' 6 tables, Unaccounted's 5 tables) were
originally built around an in-memory "dict of all current rows" pattern
(load everything -> mutate the dict -> save the whole dict back), which
this helper adapts to soft-delete semantics: existing rows are updated and
revived (is_deleted=False) if their key reappears, rows whose key vanished
from the dict get soft-deleted instead of removed, and brand-new keys are
inserted.
"""

from typing import Any, Hashable


def seed_missing_keyed_rows(
    db,
    model: type,
    key_fields: tuple[str, ...],
    rows_by_key: dict[Hashable, dict[str, Any]],
) -> int:
    """Insert seed rows whose natural keys have never existed.

    Unlike :func:`sync_keyed_rows`, this is deliberately additive: an
    existing active row is never overwritten and an archived row is never
    revived.  This makes startup seeds safe after administrators have edited
    or archived mappings in the application.

    Does not commit - caller is responsible for db.commit().
    """
    existing = {
        tuple(getattr(row, field) for field in key_fields)
        for row in db.query(model).all()
    }
    inserted = 0
    for key, fields in rows_by_key.items():
        key_t = key if isinstance(key, tuple) else (key,)
        if key_t in existing:
            continue
        row = model(**dict(zip(key_fields, key_t)), **fields)
        row.is_deleted = False
        db.add(row)
        existing.add(key_t)
        inserted += 1
    return inserted


def sync_keyed_rows(
    db,
    model: type,
    key_fields: tuple[str, ...],
    rows_by_key: dict[Hashable, dict[str, Any]],
) -> None:
    """Sync `model` rows to match `rows_by_key` (key -> {column: value}),
    soft-delete-aware. `key` may be a single value (for a 1-column key) or a
    tuple (for a composite key matching `key_fields`).

    Does not commit - caller is responsible for db.commit().
    """
    existing: dict[tuple, Any] = {}
    for row in db.query(model).all():
        k = tuple(getattr(row, f) for f in key_fields)
        existing[k] = row

    seen: set[tuple] = set()
    for key, fields in rows_by_key.items():
        key_t = key if isinstance(key, tuple) else (key,)
        seen.add(key_t)
        row = existing.get(key_t)
        if row is None:
            row = model(**dict(zip(key_fields, key_t)))
            db.add(row)
            existing[key_t] = row
        for col, value in fields.items():
            setattr(row, col, value)
        row.is_deleted = False

    for key_t, row in existing.items():
        if key_t not in seen and not row.is_deleted:
            row.is_deleted = True


def upsert_keyed_row(
    db,
    model: type,
    key_fields: tuple[str, ...],
    key: Hashable,
    fields: dict[str, Any],
) -> None:
    """Insert or update exactly ONE row identified by its natural key,
    soft-delete-aware (revives it if it was archived). Unlike
    sync_keyed_rows, this never reads or touches any other row in the table,
    so two concurrent upserts for two different keys can never race each
    other's writes - each just does its own targeted SELECT + INSERT/UPDATE.

    Use this (not sync_keyed_rows) for any "add/edit one row" endpoint. A
    real incident: several one-row mapping endpoints used to load the WHOLE
    table into memory, change one entry, and write the whole table back via
    sync_keyed_rows - fixing two different rows back-to-back (completely
    normal usage) raced, and whichever save landed last silently
    soft-deleted the other one's fix, since its snapshot was read before the
    other's write had committed.

    Does not commit - caller is responsible for db.commit()/flush().
    """
    key_t = key if isinstance(key, tuple) else (key,)
    filters = [getattr(model, f) == v for f, v in zip(key_fields, key_t)]
    row = db.query(model).filter(*filters).first()
    if row is None:
        row = model(**dict(zip(key_fields, key_t)))
        db.add(row)
    for col, value in fields.items():
        setattr(row, col, value)
    row.is_deleted = False


def delete_keyed_row(db, model: type, key_fields: tuple[str, ...], key: Hashable) -> bool:
    """Soft-delete exactly ONE row identified by its natural key, without
    touching any other row. Returns False (no-op) if no active row exists
    for that key.

    Does not commit - caller is responsible for db.commit()/flush().
    """
    key_t = key if isinstance(key, tuple) else (key,)
    filters = [getattr(model, f) == v for f, v in zip(key_fields, key_t)]
    row = db.query(model).filter(*filters).first()
    if row is None or row.is_deleted:
        return False
    row.is_deleted = True
    return True


def list_archived_rows(db, model: type):
    """Return every archived (soft-deleted) row for the restorable archive."""
    return db.query(model).filter(model.is_deleted == True).all()  # noqa: E712


def restore_keyed_row(db, model: type, key_fields: tuple[str, ...], key: Hashable) -> bool:
    """Revive exactly ONE archived row (is_deleted -> False), without
    touching any other row. Returns False if no archived row exists for
    that key.

    Does not commit - caller is responsible for db.commit()/flush().
    """
    key_t = key if isinstance(key, tuple) else (key,)
    filters = [getattr(model, f) == v for f, v in zip(key_fields, key_t)]
    row = db.query(model).filter(*filters).first()
    if row is None or not row.is_deleted:
        return False
    row.is_deleted = False
    return True
