"""SQLAlchemy models for the Trial Balance tool's 4 mapping tables.

These replace the desktop app's location-mapping.xlsx sheets (see
mapping_store.py's module docstring for the original sheet layout); data now
lives in the suite's shared MySQL database instead of a per-desktop Excel
file, so concurrent LAN edits are handled by the database rather than a file
lock. Table names are prefixed "tb_" to avoid colliding with other tools'
tables in the same shared database.

Cascade
───────
Location Code → Location Name → Region → Accounts Incharge
Account Code  → Head Office Assigned Person   (independent lookup)

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


class LocationCodeMap(Base):
    """"Location Code Map" sheet: Location Code (Distribution Account
    segment 3) -> Location Name."""
    __tablename__ = "tb_location_code_map"

    id            = Column(Integer, primary_key=True)
    location_code = Column(String(255), unique=True, index=True, nullable=False)
    location_name = Column(String(255), nullable=False, default="")
    is_deleted    = Column(Boolean, default=False, nullable=False)


class LocationRegionMap(Base):
    """"Location Region Map" sheet: Location Name -> Region."""
    __tablename__ = "tb_location_region_map"

    id            = Column(Integer, primary_key=True)
    location_name = Column(String(255), unique=True, index=True, nullable=False)
    region        = Column(String(255), nullable=False, default="")
    is_deleted    = Column(Boolean, default=False, nullable=False)


class RegionInchargeMap(Base):
    """"Region Incharge Map" sheet: Region -> Accounts Incharge (one
    incharge per region, shared by every location in it)."""
    __tablename__ = "tb_region_incharge_map"

    id                = Column(Integer, primary_key=True)
    region            = Column(String(255), unique=True, index=True, nullable=False)
    accounts_incharge = Column(String(255), nullable=False, default="")
    is_deleted        = Column(Boolean, default=False, nullable=False)


class AccountHoMap(Base):
    """"Account HO Map" sheet: Account Code -> Head Office Assigned Person.
    Independent of Location/Region — looked up directly by the natural GL
    Account Code."""
    __tablename__ = "tb_account_ho_map"

    id           = Column(Integer, primary_key=True)
    account_code = Column(String(255), unique=True, index=True, nullable=False)
    ho_person    = Column(String(255), nullable=False, default="")
    is_deleted   = Column(Boolean, default=False, nullable=False)
