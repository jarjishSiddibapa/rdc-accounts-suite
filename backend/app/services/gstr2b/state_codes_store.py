"""Persistence layer for the GSTR-2B Combinator's state-code mapping table -
backed by the suite's shared MySQL database (see models.py's StateCode
model) instead of the desktop app's hardcoded module-level ``STATE_CODES``
dict.

Internal in-memory format (unchanged from the original app.py so
combiner.py's business logic needs zero changes beyond taking the dict as a
parameter instead of importing the module constant)
────────────────────────────────────────────────────────────────────────────
state_codes : { code(int): name(str) }
"""

from sqlalchemy.orm import Session

from app.soft_delete import delete_keyed_row, sync_keyed_rows, upsert_keyed_row

from .models import StateCode

# Verbatim transcription of the original desktop app's STATE_CODES constant
# (sumeet-sir-gstr-2b-file-combinator/app.py, lines 26-48) - exactly 39
# entries. Code 25 is intentionally absent (not a real GST state code).
# Used only to seed the database on first run; the live source of truth is
# the gstr2b_state_codes MySQL table from then on.
_SEED_STATE_CODES: dict[int, str] = {
    1: "Jammu and Kashmir",        2: "Himachal Pradesh",
    3: "Punjab",                   4: "Chandigarh",
    5: "Uttarakhand",              6: "Haryana",
    7: "Delhi",                    8: "Rajasthan",
    9: "Uttar Pradesh",           10: "Bihar",
    11: "Sikkim",                 12: "Arunachal Pradesh",
    13: "Nagaland",               14: "Manipur",
    15: "Mizoram",                16: "Tripura",
    17: "Meghalaya",              18: "Assam",
    19: "West Bengal",            20: "Jharkhand",
    21: "Odisha",                 22: "Chhattisgarh",
    23: "Madhya Pradesh",         24: "Gujarat",
    26: "Dadra and Nagar Haveli and Daman and Diu",
    27: "Maharashtra",            28: "Andhra Pradesh",
    29: "Karnataka",              30: "Goa",
    31: "Lakshadweep",            32: "Kerala",
    33: "Tamil Nadu",             34: "Puducherry",
    35: "Andaman and Nicobar Islands",
    36: "Telangana",              37: "Andhra Pradesh (New)",
    38: "Ladakh",                 97: "Other Territory",
    99: "Centre Jurisdiction",
}


def _seed_if_empty(db: Session) -> None:
    """First-run only: if the state codes table is completely empty (a
    brand-new MySQL database), seed it with the original desktop app's 39
    hardcoded GST state codes so the suite launches with the same data the
    desktop app shipped with."""
    if db.query(StateCode.id).first() is not None:
        return
    for code, name in _SEED_STATE_CODES.items():
        db.add(StateCode(code=code, name=name, is_deleted=False))
    db.commit()


def load_all(db: Session) -> dict[int, str]:
    """Return {code: name} for every active (non-soft-deleted) state code,
    seeding the table from the original 39 entries on first run."""
    _seed_if_empty(db)
    return {
        row.code: row.name
        for row in db.query(StateCode).filter(StateCode.is_deleted == False).all()  # noqa: E712
    }


def save_all(db: Session, codes: dict[int, str]) -> None:
    """Sync the gstr2b_state_codes table to match `codes`, soft-delete-aware
    (see app/soft_delete.py): a code missing from `codes` gets
    is_deleted=True instead of being removed; a code that reappears (even if
    previously soft-deleted) is revived and its name updated in place."""
    sync_keyed_rows(db, StateCode, ("code",), {
        code: {"name": name} for code, name in codes.items()
    })
    db.commit()


# ── Single-row CRUD (safe under concurrent edits — see app/soft_delete.py's
#    upsert_keyed_row/delete_keyed_row). save_all() above always rewrites the
#    whole table from one combined in-memory snapshot - adding/editing/
#    deleting a single code used to go through load_all() -> mutate one
#    entry -> save_all(), which races: two such requests close together
#    (completely normal usage) can each read a snapshot that doesn't yet
#    include the other's write, and whichever save lands last silently
#    soft-deletes the other one's row. ─────────────────────────────────────

def state_code_exists(db: Session, code: int) -> bool:
    return db.query(StateCode).filter(
        StateCode.code == code, StateCode.is_deleted == False,  # noqa: E712
    ).first() is not None


def upsert_state_code(db: Session, code: int, name: str) -> None:
    upsert_keyed_row(db, StateCode, ("code",), code, {"name": name})
    db.commit()


def delete_state_code(db: Session, code: int) -> bool:
    result = delete_keyed_row(db, StateCode, ("code",), code)
    db.commit()
    return result
