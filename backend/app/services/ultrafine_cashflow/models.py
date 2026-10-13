"""SQLAlchemy models for the Ultrafine Cashflow Generator.

The schema is deliberately normalized (3NF): every business fact lives in
exactly one row, and everything that can be derived is derived at read time
instead of being copied onto the rows that use it.

Master data (each name is typed in exactly one place):

  ufcf_cf_location      CF locations (Main Operation, Flyash Business ...),
                        together with their Summary-sheet settings
                        (authorized person, payable/reserve %, group).
  ufcf_summary_group    Optional subtotal groups of CF locations.
  ufcf_location         Tally / ledger locations (Gujarat, Wada ...) and the
                        CF location each one rolls up to by default.
  ufcf_party            The "Name" of a counterparty. Holds the party's
                        default location, an optional CF-location override
                        and an optional sharing group.
  ufcf_description_alias   Statement description key -> party. Many keys can
                        point at one party; the party is never retyped.
  ufcf_sharing_group    RDC / IOCL / ETC Pool ...
  ufcf_sharing_ratio_set / _line
                        Dated percentage splits of a sharing group. A ratio
                        change is a new set with a later effective date, so
                        history is never rewritten.

Bank data (raw facts only):

  ufcf_bank_account     One row per account number found on a statement.
  ufcf_statement_upload One row per uploaded statement file.
  ufcf_transaction      One row per bank line, de-duplicated across
                        overlapping statements. Carries NO derived values:
                        name, location and CF location are always resolved
                        from the mappings, so editing a mapping instantly
                        changes every affected transaction.
  ufcf_txn_split        Optional manual allocation of one transaction over
                        locations (e.g. a "Multiple Payees" batch debit).

Output / monthly inputs:

  ufcf_cashflow_file    The latest generated workbook of each month.
  ufcf_month_input      Optional per-month opening-balance override and the
                        manually keyed statutory amount of a CF location.
  ufcf_plan_section / ufcf_plan_item
                        The editable "Particulars / Amt in Lacs" planning
                        block shown under the Summary table.

Every table carries is_deleted: the application never hard-deletes business
rows (see app/soft_delete.py).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.utcnow()


# ── master data ─────────────────────────────────────────────────────────────

class SummaryGroup(Base):
    """A subtotal group on the Summary sheet (e.g. "Panagarh" groups the two
    Aggregate CF locations and prints a "Total Panagarh" row)."""

    __tablename__ = "ufcf_summary_group"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class CfLocation(Base):
    """A cash-flow location. Also carries the Summary sheet's per-location
    settings, because each of them depends on the CF location alone."""

    __tablename__ = "ufcf_cf_location"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    show_in_summary = Column(Boolean, default=True, nullable=False)
    authorized_person = Column(String(255), nullable=False, default="")
    # Share of the collection that is available for payment (80, 95, 100 ...)
    payable_pct = Column(Numeric(7, 3), nullable=False, default=100)
    # Share of the collection held back as reserve; NULL = not shown.
    reserve_pct = Column(Numeric(7, 3), nullable=True)
    group_id = Column(Integer, ForeignKey("ufcf_summary_group.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class Location(Base):
    """A Tally / ledger location and the CF location it normally rolls up to."""

    __tablename__ = "ufcf_location"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    cf_location_id = Column(Integer, ForeignKey("ufcf_cf_location.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class SharingGroup(Base):
    """A counterparty whose money is split by percentage (RDC, IOCL ...)."""

    __tablename__ = "ufcf_sharing_group"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class Party(Base):
    """The "Name" a bank description resolves to."""

    __tablename__ = "ufcf_party"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    location_id = Column(Integer, ForeignKey("ufcf_location.id"), nullable=True)
    # Only set when this party's CF location differs from its location's
    # default CF location - otherwise the CF location is derived.
    cf_location_id = Column(Integer, ForeignKey("ufcf_cf_location.id"), nullable=True)
    # When set, location/CF location are ignored and the amount is split by
    # the sharing group's ratio set in force on the transaction date.
    sharing_group_id = Column(Integer, ForeignKey("ufcf_sharing_group.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class DescriptionAlias(Base):
    """Normalized statement-description key -> party (see keys.py)."""

    __tablename__ = "ufcf_description_alias"

    id = Column(Integer, primary_key=True)
    desc_key = Column(String(500), unique=True, nullable=False)
    party_id = Column(Integer, ForeignKey("ufcf_party.id"), nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class SharingRatioSet(Base):
    """One dated version of a sharing group's percentages."""

    __tablename__ = "ufcf_sharing_ratio_set"
    __table_args__ = (UniqueConstraint("group_id", "effective_from", name="uq_ufcf_ratio_set"),)

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("ufcf_sharing_group.id"), nullable=False, index=True)
    effective_from = Column(DateTime, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class SharingRatioLine(Base):
    """One destination of a ratio set."""

    __tablename__ = "ufcf_sharing_ratio_line"
    __table_args__ = (UniqueConstraint("set_id", "location_id", name="uq_ufcf_ratio_line"),)

    id = Column(Integer, primary_key=True)
    set_id = Column(Integer, ForeignKey("ufcf_sharing_ratio_set.id"), nullable=False, index=True)
    location_id = Column(Integer, ForeignKey("ufcf_location.id"), nullable=False)
    # Only when this line must land on a CF location other than the
    # location's own default (e.g. ETC Pool's Aggregate share).
    cf_location_id = Column(Integer, ForeignKey("ufcf_cf_location.id"), nullable=True)
    ratio = Column(Float(53), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


# ── bank data ───────────────────────────────────────────────────────────────

class BankAccount(Base):
    __tablename__ = "ufcf_bank_account"

    id = Column(Integer, primary_key=True)
    account_no = Column(String(64), unique=True, nullable=False)
    label = Column(String(255), nullable=False, default="")
    is_deleted = Column(Boolean, default=False, nullable=False)


class StatementUpload(Base):
    __tablename__ = "ufcf_statement_upload"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("ufcf_bank_account.id"), nullable=False, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=_utcnow, nullable=False)
    filename = Column(String(255), nullable=False)
    period_from = Column(Date, nullable=True)
    period_to = Column(Date, nullable=True)
    line_count = Column(Integer, nullable=False, default=0)
    new_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    is_deleted = Column(Boolean, default=False, nullable=False)


class Transaction(Base):
    __tablename__ = "ufcf_transaction"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("ufcf_bank_account.id"), nullable=False, index=True)
    upload_id = Column(Integer, ForeignKey("ufcf_statement_upload.id"), nullable=False)
    txn_dt = Column(DateTime, nullable=False, index=True)
    direction = Column(String(1), nullable=False)  # "D" = payment, "C" = receipt
    amount = Column(Numeric(18, 2), nullable=False)
    description = Column(Text, nullable=False)
    reference_no = Column(String(64), nullable=False, default="")
    # SHA-1 of account|date|HH:MM|direction|amount|description|occurrence.
    dedupe_key = Column(String(40), unique=True, nullable=False)
    # Manual override of the party the description would resolve to.
    party_id = Column(Integer, ForeignKey("ufcf_party.id"), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class TransactionSplit(Base):
    """Manual allocation of a transaction over locations. When present it
    replaces the party/sharing based allocation entirely."""

    __tablename__ = "ufcf_txn_split"

    id = Column(Integer, primary_key=True)
    txn_id = Column(Integer, ForeignKey("ufcf_transaction.id"), nullable=False, index=True)
    location_id = Column(Integer, ForeignKey("ufcf_location.id"), nullable=False)
    cf_location_id = Column(Integer, ForeignKey("ufcf_cf_location.id"), nullable=True)
    amount = Column(Numeric(18, 2), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


# ── monthly output and inputs ───────────────────────────────────────────────

class CashflowFile(Base):
    __tablename__ = "ufcf_cashflow_file"

    id = Column(Integer, primary_key=True)
    month = Column(String(7), unique=True, nullable=False)  # "2026-09"
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(1024), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    txn_count = Column(Integer, nullable=False, default=0)
    source = Column(String(20), nullable=False, default="generated")  # "generated" | "uploaded"
    generated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    generated_at = Column(DateTime, default=_utcnow, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class MonthInput(Base):
    __tablename__ = "ufcf_month_input"
    __table_args__ = (UniqueConstraint("month", "cf_location_id", name="uq_ufcf_month_input"),)

    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)
    cf_location_id = Column(Integer, ForeignKey("ufcf_cf_location.id"), nullable=False)
    # Lakhs. NULL = carry the previous month's closing balance forward.
    opening_balance = Column(Numeric(18, 4), nullable=True)
    statutory_amount = Column(Numeric(18, 4), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class PlanSection(Base):
    __tablename__ = "ufcf_plan_section"

    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)
    block_no = Column(Integer, nullable=False, default=1)
    title = Column(String(255), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_deleted = Column(Boolean, default=False, nullable=False)


class PlanItem(Base):
    __tablename__ = "ufcf_plan_item"

    id = Column(Integer, primary_key=True)
    section_id = Column(Integer, ForeignKey("ufcf_plan_section.id"), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    # A number or a simple arithmetic expression such as "20+3+1.5".
    amount_expr = Column(String(255), nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    is_deleted = Column(Boolean, default=False, nullable=False)
