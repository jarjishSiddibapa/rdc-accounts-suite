"""SQLAlchemy ORM models for the suite's shared database."""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)  # 'admin' or 'user'
    created_at = Column(DateTime, default=datetime.utcnow)

    # Active/inactive is a reversible admin toggle that blocks login without
    # touching the account (e.g. someone on leave); is_deleted is the
    # application-wide soft-delete flag - nothing is ever hard-deleted, see
    # app/soft_delete.py. Both block login; they're tracked separately
    # because they mean different things to an admin managing users.
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)

    # JSON-encoded list of app keys this user may use (see app/permissions.py
    # for the canonical key list). Regular users default to an empty list and
    # receive access only after an admin explicitly grants it. Legacy NULL
    # values are also interpreted as an empty list. Admins always have full
    # access regardless of this field.
    allowed_apps = Column(Text, nullable=True)

    email_settings = relationship(
        "EmailSettings", back_populates="user", uselist=False,
        cascade="save-update, merge",
    )


class Application(Base):
    """Admin-owned metadata for an application in the suite.

    Routing keys remain stable in code, while business ownership can be
    changed centrally without redeploying the frontend. Rows follow the
    suite-wide soft-delete policy.
    """

    __tablename__ = "applications"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    label = Column(String(255), nullable=False)
    company = Column(String(32), nullable=False)  # 'RDC' or 'Ultrafine'
    collaborator = Column(String(255), nullable=True)  # shown in the footer as "Made by Jarjish & {collaborator}"
    is_deleted = Column(Boolean, default=False, nullable=False)


class EmailSettings(Base):
    """Per-user email sender identity — each user configures their own Gmail
    address + app password from their own login; nothing here is shared
    across accounts."""
    __tablename__ = "email_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    sender_email = Column(String(255), nullable=True)
    app_password_encrypted = Column(String(255), nullable=True)
    signature = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="email_settings")


class ReportRecipientDefaults(Base):
    """Who generated reports get emailed to — an application-wide business
    rule (e.g. "always CC the accounts group"), NOT a per-user preference:
    whoever is logged in and clicks Send, the recipients stay the same.
    Admin-editable. Singleton row, id=1."""
    __tablename__ = "report_recipient_defaults"

    id = Column(Integer, primary_key=True)
    default_to = Column(Text, nullable=True)  # JSON-encoded list[str]
    default_cc = Column(Text, nullable=True)  # JSON-encoded list[str]
    is_deleted = Column(Boolean, default=False, nullable=False)


class ApplicationEmailRecipient(Base):
    """Admin-managed default To/Cc recipients for one application.

    The unique constraint prevents the same address from appearing twice for
    an application, including once in To and again in Cc. Removing an address
    only sets ``is_deleted``; a later save revives the same row.
    """

    __tablename__ = "application_email_recipients"
    __table_args__ = (
        UniqueConstraint("app_key", "email", name="uq_application_recipient_email"),
    )

    id = Column(Integer, primary_key=True)
    app_key = Column(String(64), index=True, nullable=False)
    recipient_type = Column(String(8), nullable=False)  # 'to' or 'cc'
    email = Column(String(255), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class SystemEmailSettings(Base):
    """The single application-level sender identity (distinct from each
    user's own EmailSettings above) — used for password-reset emails and
    other system notifications, not for sending generated reports. Admin
    panel only. Singleton row, id=1."""
    __tablename__ = "system_email_settings"

    id = Column(Integer, primary_key=True)
    sender_email = Column(String(255), nullable=True)
    app_password_encrypted = Column(String(255), nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)


class AuditLog(Base):
    """One row per recorded action across the whole suite: server
    lifecycle events, logins, and every API request (who did what, when).
    Written automatically by main.py's request-logging middleware, so
    individual routers never need to remember to log anything themselves."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Denormalized snapshot of the actor's email at the time of the action,
    # so the log stays readable even if that user account is later deleted.
    actor_email = Column(String(255), nullable=True)
    action = Column(String(255), nullable=False)  # e.g. "server.start", "POST /api/tools/.../process"
    status_code = Column(Integer, nullable=True)
    ip_address = Column(String(64), nullable=True)
    details = Column(Text, nullable=True)  # free-form JSON-encoded extra context
    is_deleted = Column(Boolean, default=False, nullable=False)


class BackupSettings(Base):
    """Admin-configurable schedule for automatic full database backups.
    Singleton row, id=1."""
    __tablename__ = "backup_settings"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, default=True, nullable=False)
    backup_time = Column(String(5), default="03:00", nullable=False)  # "HH:MM", 24h, IST
    max_backups = Column(Integer, default=30, nullable=False)
    # How long a job's scratch upload/output files sit under backend/data/scratch
    # before the scheduler's sweep deletes them (see app/scheduler.py's
    # _sweep_scratch) - a backstop for jobs whose own router cleanup never ran
    # (error before cleanup, or a result nobody ever downloaded).
    scratch_cleanup_hours = Column(Integer, default=6, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
