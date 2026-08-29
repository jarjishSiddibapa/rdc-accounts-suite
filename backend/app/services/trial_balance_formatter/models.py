"""Central MySQL ledger classifications for the Trial Balance Formatter."""

from sqlalchemy import Boolean, Column, Integer, String

from app.database import Base


class LedgerNature(Base):
    """Central authoritative Debit/Credit and subgroup classification."""

    __tablename__ = "trial_balance_formatter_ledger_natures"

    id = Column(Integer, primary_key=True)
    ledger_key = Column(String(255), unique=True, index=True, nullable=False)
    ledger_name = Column(String(255), nullable=False)
    nature = Column(String(2), nullable=False, default="Dr")  # "Dr" or "Cr"
    # Tally exports do not identify subtotal rows explicitly.  The reference
    # workbook intentionally highlights these rows and leaves TB Balance blank
    # to avoid double-counting their children, so that decision is part of the
    # centralized mapping rather than a process-local heuristic.
    is_subgroup = Column(Boolean, nullable=False, default=False)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
