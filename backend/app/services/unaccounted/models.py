"""SQLAlchemy models for the Unaccounted Transactions tool's user-editable
mapping tables.

These replace the desktop app's mappings.json / po_keywords.json /
po_excluded.json files. site_mapping.py's ~1,131-entry SITE_MAPPING dict is
NOT ported here — it stays a hardcoded Python constant (base/fallback data,
not user-editable) and processing.py already merges it with these DB-backed
overrides at read time.

Imported by app.routers.unaccounted_txn so SQLAlchemy registers these tables
before app.database.init_db()'s Base.metadata.create_all() runs.
"""

from sqlalchemy import Boolean, Column, Float, Integer, String

from app.database import Base

# Every keyed table below carries is_deleted: the whole application uses
# soft deletes (see app/soft_delete.py) - "removing" an entry just flags it,
# never actually deletes the row. PoKeywordSettings is a true singleton
# settings row is updated in place, but still carries is_deleted so every
# persisted business entity follows the same retention policy.


class SiteOverride(Base):
    """User-added Supplier Site -> (Location, Accounts Incharge) overrides —
    layered on top of the hardcoded SITE_MAPPING base table."""
    __tablename__ = "unaccounted_site_overrides"

    id = Column(Integer, primary_key=True)
    supplier_site = Column(String(255), unique=True, index=True, nullable=False)
    location = Column(String(255), nullable=False)
    accounts_incharge = Column(String(255), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class CreatorMapping(Base):
    """Created By -> (Location, Accounts Incharge), used to infer mapping
    for OFFICE / HOME / TDS TAX Authori supplier sites."""
    __tablename__ = "unaccounted_creator_mapping"

    id = Column(Integer, primary_key=True)
    created_by = Column(String(255), unique=True, index=True, nullable=False)
    location = Column(String(255), nullable=False)
    accounts_incharge = Column(String(255), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class LocationIncharge(Base):
    """The one-time Location -> Accounts Incharge table: mapped once per
    Location and reused everywhere (Supplier Site overrides, Created By
    mapping, and any newly-fixed mapping) via _resolve_incharge()."""
    __tablename__ = "unaccounted_location_incharge"

    id = Column(Integer, primary_key=True)
    location = Column(String(255), unique=True, index=True, nullable=False)
    accounts_incharge = Column(String(255), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class PoKeyword(Base):
    """Fuzzy-match keyword list for the Uninvoiced Expense PO report's
    'Excluded POs' filter (e.g. 'land rent', 'guest')."""
    __tablename__ = "unaccounted_po_keywords"

    id = Column(Integer, primary_key=True)
    keyword = Column(String(255), unique=True, index=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class PoKeywordSettings(Base):
    """Single-row table holding the fuzzy-match threshold, kept separate
    from the keyword list itself."""
    __tablename__ = "unaccounted_po_keyword_settings"

    id = Column(Integer, primary_key=True)
    threshold = Column(Float, nullable=False, default=0.82)
    is_deleted = Column(Boolean, default=False, nullable=False)


class ExcludedPo(Base):
    """PO numbers permanently excluded from the main Uninvoiced Expense PO
    report output."""
    __tablename__ = "unaccounted_excluded_pos"

    id = Column(Integer, primary_key=True)
    po_number = Column(String(255), unique=True, index=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
