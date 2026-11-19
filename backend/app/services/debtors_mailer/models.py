"""SQLAlchemy models for the Debtors Reports Auto Mailer: its 2 mapping tables and its mail plans.

The mailer builds the Untagged Invoices and the Unapplied Receipts reports in
one go, so it keeps ONE pair of mappings for both (the standalone tools keep
their own; see app.services.untagged_invoices.models for why each tool owns
its copy). They are seeded once from those tools' current mappings
(mapping_store.py), after which they are edited here only.
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text

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


class MailPlan(Base):
    """One scheduled mail of the Debtors Reports Auto Mailer: which of the three reports it sends, the date they are
    as on, when it goes out, to whom, and the wording. An application can have several (a daily mail to the team,
    a month-end pack for the meeting, ...). The schedule and run-state columns are named exactly like those of
    ScheduledReportMail, so the scheduler's functions work on either."""

    __tablename__ = "debtors_mail_plan"

    id         = Column(Integer, primary_key=True)
    name       = Column(String(60), nullable=False)
    # what it sends
    ageing     = Column(Boolean, default=True, nullable=False)      # the ageing with the GST invoice numbers
    unapplied  = Column(Boolean, default=True, nullable=False)      # the Unapplied Receipts report
    untagged   = Column(Boolean, default=True, nullable=False)      # the Untagged Invoices report (with Below 1k)
    # the date the reports are as on: "today", "last_month_end" or a fixed "date"
    as_on_mode = Column(String(20), default="today", nullable=False)
    as_on_date = Column(Date, nullable=True)
    # who gets it (JSON lists of addresses)
    to_list    = Column(Text, nullable=True)
    cc_list    = Column(Text, nullable=True)
    # when
    enabled    = Column(Boolean, default=False, nullable=False)
    send_mode  = Column(String(10), default="weekly", nullable=False)
    send_days  = Column(String(20), default="0,1,2,3,4,5", nullable=False)
    send_month_days = Column(String(100), nullable=True)
    send_time  = Column(String(5), default="08:30", nullable=False)
    send_times = Column(String(100), nullable=True)
    send_times_enabled = Column(Boolean, default=True, nullable=False)
    send_every_hours   = Column(Integer, nullable=True)
    send_every_start   = Column(String(5), nullable=True)
    last_every_slot    = Column(String(16), nullable=True)
    # the wording
    subject_template = Column(Text, nullable=False)
    intro_template   = Column(Text, nullable=False)
    signature        = Column(Text, nullable=True)
    send_when_unmapped = Column(Boolean, default=True, nullable=False)
    # the state of the runs
    last_attempt_date = Column(Date, nullable=True)
    last_attempt_slot = Column(String(16), nullable=True)
    running_since     = Column(DateTime, nullable=True)
    last_run_at       = Column(DateTime, nullable=True)
    last_trigger      = Column(String(20), nullable=True)
    last_status       = Column(String(20), nullable=True)
    last_error        = Column(Text, nullable=True)
    last_detail       = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String(255), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)

    # an application's mail has no sender choice here: it always goes from the system email
    sender_user_id = None
