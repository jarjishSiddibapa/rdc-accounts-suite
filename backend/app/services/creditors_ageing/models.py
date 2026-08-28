"""Central MySQL mapping model for the Creditors Ageing tool."""

from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base


class VendorMapping(Base):
    """Vendor classification used by every Creditors Ageing report run.

    ``vendor_key`` is the normalized, case-insensitive identity inherited
    from the desktop application.  ``vendor_name`` preserves the spelling
    shown in reports.  Rows are archived through ``is_deleted`` only.
    """

    __tablename__ = "creditors_ageing_vendor_mappings"

    id = Column(Integer, primary_key=True)
    vendor_key = Column(String(255), unique=True, index=True, nullable=False)
    vendor_name = Column(String(255), nullable=False)
    location = Column(String(255), nullable=False, default="")
    vendor_type = Column(String(255), nullable=False, default="")
    vendor_sub_type = Column(String(255), nullable=False, default="")
    intercompany = Column(Boolean, nullable=False, default=False)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

