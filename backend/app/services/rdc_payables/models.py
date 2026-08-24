"""SQLAlchemy models for the RDC Payables Report tool's 6 mapping tables.

These replace the desktop app's vendor-site-code-mapping.xlsx sheets
(see mapping_store.py's module docstring for the original sheet layout);
data now lives in the suite's shared MySQL database instead of a per-desktop
Excel file, so concurrent LAN edits are handled by the database rather than
a file lock. Table names are prefixed "rdc_" to avoid colliding with other
tools' tables in the same shared database.

Importing this module is enough for SQLAlchemy to register these models
against `app.database.Base` — the existing `Base.metadata.create_all()` call
in `database.init_db()` then creates them automatically. No extra
create_all() call is needed here.
"""

from sqlalchemy import Boolean, Column, Integer, String, UniqueConstraint

from app.database import Base

# Every table below carries is_deleted: the whole application uses soft
# deletes (see app/soft_delete.py) - "removing" a mapping row just flags it,
# never actually deletes it. mapping_store.py's load_all() only reads
# is_deleted=False rows; save_all() revives a row (is_deleted=False) if its
# key reappears instead of inserting a duplicate.


class VendorSiteMapping(Base):
    """"Vendor Site Codes" sheet: Vendor Site Code -> Region -> Accounts
    Incharge."""
    __tablename__ = "rdc_vendor_site_mapping"

    id                = Column(Integer, primary_key=True)
    vendor_site_code  = Column(String(255), unique=True, index=True, nullable=False)
    region            = Column(String(255), nullable=False, default="")
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)


class LocationCodeMap(Base):
    """"Location Code Map" sheet: Location Code (Distribution Account
    segment 3) -> Region -> Accounts Incharge."""
    __tablename__ = "rdc_location_code_map"

    id                = Column(Integer, primary_key=True)
    location_code     = Column(String(255), unique=True, index=True, nullable=False)
    region            = Column(String(255), nullable=False, default="")
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)


class RowExclusion(Base):
    """"Row Exclusions" sheet: rows matching Vendor Number (+ optionally
    Invoice Number) are dropped from the output. A blank invoice_number
    means "every invoice from this vendor"."""
    __tablename__ = "rdc_row_exclusion"
    __table_args__ = (
        UniqueConstraint("vendor_number", "invoice_number", name="uq_rdc_row_exclusion_vn_inv"),
    )

    id             = Column(Integer, primary_key=True)
    vendor_number  = Column(String(255), index=True, nullable=False)
    invoice_number = Column(String(255), nullable=True, default="")
    is_deleted     = Column(Boolean, default=False, nullable=False)


class InvoiceOverride(Base):
    """"Invoice Overrides" sheet: Vendor Number (+ optionally Invoice
    Number) -> Region -> Accounts Incharge, overriding the Vendor Site Code
    mapping. A blank invoice_number means "every invoice from this
    vendor"."""
    __tablename__ = "rdc_invoice_override"
    __table_args__ = (
        UniqueConstraint("vendor_number", "invoice_number", name="uq_rdc_invoice_override_vn_inv"),
    )

    id                = Column(Integer, primary_key=True)
    vendor_number     = Column(String(255), index=True, nullable=False)
    invoice_number    = Column(String(255), nullable=True, default="")
    region            = Column(String(255), nullable=False, default="")
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)


class RegionInchargeMap(Base):
    """"Region Incharge Map" sheet: Region -> Accounts Incharge (one
    incharge per region; always wins over vendor/location/invoice-derived
    incharge values)."""
    __tablename__ = "rdc_region_incharge_map"

    id                = Column(Integer, primary_key=True)
    region            = Column(String(255), unique=True, index=True, nullable=False)
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)


class TransactionTypeOverride(Base):
    """"Transaction Type Overrides" sheet: Vendor Number (+ optionally
    Invoice Number) -> fixed Transaction Type, overriding the automatic
    IOCL/TDS/Prepayment/Security Deposit/Other classification. A blank
    invoice_number means "every invoice from this vendor"."""
    __tablename__ = "rdc_transaction_type_override"
    __table_args__ = (
        UniqueConstraint("vendor_number", "invoice_number", name="uq_rdc_txn_type_override_vn_inv"),
    )

    id               = Column(Integer, primary_key=True)
    vendor_number    = Column(String(255), index=True, nullable=False)
    invoice_number   = Column(String(255), nullable=True, default="")
    transaction_type = Column(String(255), nullable=False, default="")
    is_deleted       = Column(Boolean, default=False, nullable=False)
