"""SQLAlchemy models for the Unapplied Receipts Report tool's 2 mapping
tables.

These replace the desktop app's two AppData JSON files
(``incharge_mapping.json`` / ``supplier_site_mapping.json`` — see
``mapping_store.py``'s module docstring) with the suite's shared MySQL
database. Table names are prefixed "unapplied_" to avoid colliding with
other tools' tables in the same shared database.

Importing this module is enough for SQLAlchemy to register these models
against `app.database.Base` — the existing `Base.metadata.create_all()` call
in `database.init_db()` then creates them automatically. No extra
create_all() call is needed here.
"""

from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base

# Every table below carries is_deleted: the whole application uses soft
# deletes (see app/soft_delete.py) - "removing" a mapping row just flags it,
# never actually deletes it. mapping_store.py's load_all() only reads
# is_deleted=False rows; save_all() revives a row (is_deleted=False) if its
# key reappears instead of inserting a duplicate.


class AccountsInchargeMap(Base):
    """Location -> Accounts Incharge.

    Matches the original desktop app's hardcoded ``ACCOUNT_INCHARGE_MAP``
    dict (source: region-account-incharge-mapping.xlsx per the original
    comment) and the user-editable copy it persisted to
    ``%APPDATA%\\RDCUnapplied\\incharge_mapping.json`` via the
    "Accounts Incharge — Mapping Manager" dialog (MappingDialog in the
    original unapplied_processor.py).
    """

    __tablename__ = "unapplied_accounts_incharge_map"

    id                = Column(Integer, primary_key=True)
    location          = Column(String(255), unique=True, index=True, nullable=False)
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)


class SupplierSiteMap(Base):
    """Raw ERP location (Oracle ``ar_cash_receipts_all.global_attribute10``)
    -> mapped display Location.

    Matches the original desktop app's hardcoded ``LOCATION_MAP`` dict (and
    its live-editable copy, ``SUPPLIER_SITE_MAP``, persisted to
    ``%APPDATA%\\RDCUnapplied\\supplier_site_mapping.json`` via the
    "Supplier Site — Mapping Manager" dialog, SupplierSiteDialog in the
    original). The original app has no separate "region" concept for this
    table — each raw ERP site name maps directly to one display Location
    string (e.g. "BHILAI" -> "CHHATTISGARH"), so this model intentionally
    has no region column.
    """

    __tablename__ = "unapplied_supplier_site_map"

    id             = Column(Integer, primary_key=True)
    supplier_site  = Column(String(255), unique=True, index=True, nullable=False)
    location       = Column(String(255), nullable=False, default="")
    is_deleted     = Column(Boolean, default=False, nullable=False)
