"""SQLAlchemy models for the Debtors Reports Auto Mailer's 2 mapping tables.

The mailer builds the Untagged Invoices and the Unapplied Receipts reports in
one go, so it keeps ONE pair of mappings for both (the standalone tools keep
their own; see app.services.untagged_invoices.models for why each tool owns
its copy). They are seeded once from those tools' current mappings
(mapping_store.py), after which they are edited here only.
"""

from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base


class LocationMap(Base):
    """Raw ERP location name (uppercased) -> Location. Used for the ageing's
    'Location Name' and for the receipts' own ERP location, which are the same
    names; only for customers the ERP holds no region for."""

    __tablename__ = "debtors_mailer_location_map"

    id            = Column(Integer, primary_key=True)
    location_name = Column(String(255), unique=True, index=True, nullable=False)
    location      = Column(String(255), nullable=False, default="")
    is_deleted    = Column(Boolean, default=False, nullable=False)


class AccountsInchargeMap(Base):
    """Location (an ERP region, or a mapped Location) -> Accounts Incharge."""

    __tablename__ = "debtors_mailer_incharge_map"

    id                = Column(Integer, primary_key=True)
    location          = Column(String(255), unique=True, index=True, nullable=False)
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)
