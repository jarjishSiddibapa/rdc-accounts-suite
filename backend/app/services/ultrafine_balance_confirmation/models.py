"""SQLAlchemy model for the Ultrafine Balance Confirmation tool's
Customer -> Email mapping table.

This mapping did NOT exist as a persistent table in the original desktop
app — customers were always re-typed into the input Excel's "To Email IDs" /
"CC Email IDs" columns on every run (see the ported app/excel/excel_reader.py
logic in processing.py). Persisting it here lets the suite remember
addresses across runs while still letting each upload add to / override them
for that particular send (see mapping_store.py and
processing.merge_with_mapping). Per the task, this table starts empty — no
seed data — matching the original's own design.

Importing this module is enough for SQLAlchemy to register it against
app.database.Base — the existing Base.metadata.create_all() call in
database.init_db() then creates the table automatically.
"""

from sqlalchemy import Boolean, Column, Integer, String, Text

from app.database import Base

# Carries is_deleted like every other table in the suite (see
# app/soft_delete.py) — removing a customer's saved email mapping only sets
# is_deleted=True; it is never actually deleted, and a later save with the
# same customer_name revives the row.


class CustomerEmailMap(Base):
    """One row per customer: their saved To/CC recipients for balance
    confirmation emails. to_emails/cc_emails are comma-separated address
    strings (matching the desktop app's own input-file column format), not
    JSON, so the values round-trip directly through the CSV-ish text fields
    the original app already used."""

    __tablename__ = "ultrafine_bc_customer_emails"

    id = Column(Integer, primary_key=True)
    customer_name = Column(String(255), unique=True, index=True, nullable=False)
    to_emails = Column(Text, nullable=False, default="")
    cc_emails = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
