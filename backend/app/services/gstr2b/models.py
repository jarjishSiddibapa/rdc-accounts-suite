"""SQLAlchemy model for the GSTR-2B Combinator tool's state-code mapping
table.

Replaces the desktop app's hardcoded ``STATE_CODES: dict[int, str]``
module-level constant (see the sibling source repo's app.py, lines 26-48)
with a user-editable table in the suite's shared MySQL database, so admins
can add/edit/remove GST state codes without a source change + exe rebuild.

Imported by app.routers.gstr2b so SQLAlchemy registers this table before
app.database.init_db()'s Base.metadata.create_all() runs.
"""

from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base

# Follows the whole application's soft-delete convention (see
# app/soft_delete.py): "removing" a state code via the CRUD UI just flags
# is_deleted, never actually deletes the row.


class StateCode(Base):
    """GST state code -> state name. ``code`` is the actual 2-digit GST
    state code embedded in a GSTR-2B filename's GSTIN segment (NOT the same
    as the auto-increment ``id`` primary key) - it is a distinct, uniquely
    indexed column so lookups by code stay simple integer equality."""

    __tablename__ = "gstr2b_state_codes"

    id         = Column(Integer, primary_key=True)
    code       = Column(Integer, unique=True, index=True, nullable=False)
    name       = Column(String(255), nullable=False, default="")
    is_deleted = Column(Boolean, default=False, nullable=False)
