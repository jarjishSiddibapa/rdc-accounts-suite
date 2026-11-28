"""Tables of RDC HSBC Tradepay CSV Generation (soft deleted, like everything in the suite)."""

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT

from app.database import Base


class HsbcTradepaySettings(Base):
    """Singleton row, id=1: the fixed value of cell E1 of the bank's template (set by an administrator; it is
    company data, so it is not part of the source code)."""

    __tablename__ = "hsbc_tradepay_settings"

    id = Column(Integer, primary_key=True)
    hsbc_id = Column(String(40), nullable=True)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class HsbcTradepayFile(Base):
    """One generated upload file. ``active_code`` carries the unique file code while the file counts; removing
    the file clears it, which frees the code. The unique index is what stops two people (two web workers) from
    making the same code."""

    __tablename__ = "hsbc_tradepay_files"

    id = Column(Integer, primary_key=True)
    file_code = Column(String(20), nullable=False, index=True)
    active_code = Column(String(20), nullable=True, unique=True)
    location = Column(String(3), nullable=False, index=True)
    series = Column(Integer, nullable=False)
    file_date = Column(Date, nullable=False)
    row_count = Column(Integer, nullable=False)
    total_amount = Column(Numeric(18, 2), nullable=False)
    csv_text = Column(Text().with_variant(LONGTEXT, "mysql"), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)


class HsbcTradepayLine(Base):
    """One payment of a generated file: what was paid to which account, and whether the account came from the
    ERP or was typed. Used to warn when an invoice is put into a second file."""

    __tablename__ = "hsbc_tradepay_lines"

    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey("hsbc_tradepay_files.id"), nullable=False, index=True)
    vendor_code = Column(String(30), nullable=False, index=True)
    invoice_no = Column(String(60), nullable=False)
    invoice_key = Column(String(60), nullable=False, index=True)
    party_name = Column(String(40), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    account_no = Column(String(40), nullable=False)
    ifsc = Column(String(20), nullable=False)
    account_source = Column(String(10), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
