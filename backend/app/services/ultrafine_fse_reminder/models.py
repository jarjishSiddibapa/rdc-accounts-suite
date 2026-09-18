"""SQLAlchemy model for the Ultrafine FSE Bulk Reminder tool's
FSE -> Email mapping table.

Starts empty - no seed data, matching the sibling ultrafine_balance_confirmation
mapping table's own documented convention (see its models.py). Guessing an
FSE's email from name similarity would risk silently mismatching or
mis-sending a real business email, so every FSE's address is entered once
by a user (either proactively via the mappings tab, or reactively via the
missing-email fix panel shown during preview) and remembered from then on.

Importing this module is enough for SQLAlchemy to register it against
app.database.Base - the existing Base.metadata.create_all() call in
database.init_db() then creates the table automatically.
"""

from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base


class FseEmailMap(Base):
    """One row per FSE: their saved email address for collection-reminder
    mail. Unlike the customer-facing mapping tables (which carry separate
    To/Cc lists), each FSE only ever needs one address - Cc is always the
    fixed, hardcoded list described in app/services/ultrafine_fse_reminder/
    processor.py, never per-FSE data.

    Carries is_deleted like every other table in the suite (see
    app/soft_delete.py) - removing an FSE's saved mapping only sets
    is_deleted=True; a later save with the same fse_name revives the row."""

    __tablename__ = "ultrafine_fse_email_map"

    id = Column(Integer, primary_key=True)
    fse_name = Column(String(255), unique=True, index=True, nullable=False)
    email = Column(String(320), nullable=False, default="")
    is_deleted = Column(Boolean, default=False, nullable=False)
