"""SQLAlchemy models for the Untagged Invoices Report tool's 2 mapping
tables.

Same shape as app.services.unapplied_receipts.models (raw ERP "Location
Name" -> display Location, and Location -> Accounts Incharge) - this tool's
input file uses the exact same "Location Name" text (Oracle AR aging
export) as the Unapplied Receipts Report's ageing file, so the two tools'
mapping data is highly overlapping in practice. Kept as this tool's own
table (seeded from the same defaults, see mapping_store.py) rather than a
shared table, matching the suite-wide convention that each tool owns and
edits its own mapping copy (see app.services.unaccounted.models's docstring
for the same reasoning) - editing one tool's mapping must never silently
change another tool's report output.
"""

from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base

# Every table below carries is_deleted: the whole application uses soft
# deletes (see app/soft_delete.py) - "removing" a mapping row just flags it,
# never actually deletes it.


class LocationMap(Base):
    """Raw ERP "Location Name" (uppercased) -> mapped display Location."""

    __tablename__ = "untagged_invoices_location_map"

    id            = Column(Integer, primary_key=True)
    location_name = Column(String(255), unique=True, index=True, nullable=False)
    location      = Column(String(255), nullable=False, default="")
    is_deleted    = Column(Boolean, default=False, nullable=False)


class AccountsInchargeMap(Base):
    """Display Location -> Accounts Incharge."""

    __tablename__ = "untagged_invoices_incharge_map"

    id                = Column(Integer, primary_key=True)
    location          = Column(String(255), unique=True, index=True, nullable=False)
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)
