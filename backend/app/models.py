"""SQLAlchemy ORM models for the suite's shared database."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
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
    # (error before cleanup, or a result nobody ever downloaded). In minutes
    # (not hours) so it can be set as low as the app's own session length -
    # e.g. 30 minutes, matching the default auto-logout time, so nothing
    # outlives a user's own session on disk.
    scratch_cleanup_minutes = Column(Integer, default=30, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)


class BackgroundJob(Base):
    """Durable work queue shared by every API and worker process."""

    __tablename__ = "background_jobs"

    id = Column(String(36), primary_key=True)
    owner_id = Column(Integer, nullable=False, index=True)
    task_name = Column(String(255), nullable=False)
    args_json = Column(LONGTEXT, nullable=False)
    kwargs_json = Column(LONGTEXT, nullable=False)
    resource_key = Column(String(64), nullable=True, index=True)
    client_tab_id = Column(String(64), nullable=True, index=True)
    client_heartbeat_at = Column(DateTime, nullable=True, index=True)
    cancel_on_disconnect = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), nullable=False, index=True)  # queued/running/done/error/cancelled
    progress = Column(Float, default=0.0, nullable=False)
    phase = Column(String(255), default="Queued", nullable=False)
    result_json = Column(LONGTEXT, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, default=False, nullable=False)
    priority = Column(Integer, default=100, nullable=False, index=True)
    not_before = Column(DateTime, nullable=True, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    lease_owner = Column(String(128), nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    heartbeat_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)


class BackgroundJobAction(Base):
    """Database-backed idempotency claim for irreversible job actions."""

    __tablename__ = "background_job_actions"
    __table_args__ = (
        UniqueConstraint("job_id", "action", name="uq_background_job_action"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(String(36), ForeignKey("background_jobs.id"), nullable=False, index=True)
    owner_id = Column(Integer, nullable=False, index=True)
    action = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)  # in_progress/completed/failed
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)


class BackgroundResourceSlot(Base):
    """A durable semaphore slot for globally constrained dependencies."""

    __tablename__ = "background_resource_slots"
    __table_args__ = (
        UniqueConstraint("resource_key", "slot_number", name="uq_background_resource_slot"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_key = Column(String(64), nullable=False, index=True)
    slot_number = Column(Integer, nullable=False)
    job_id = Column(String(36), nullable=True, index=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)


class RateLimitBucket(Base):
    """One locked row per rate-limit key, shared across all API workers."""

    __tablename__ = "rate_limit_buckets"

    limiter_key = Column(String(255), primary_key=True)
    events_json = Column(LONGTEXT, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)


class TrialBalanceUploadToken(Base):
    """Durable metadata for Trial Balance's upload -> selection workflow."""

    __tablename__ = "trial_balance_upload_tokens"

    token = Column(String(36), primary_key=True)
    owner_id = Column(Integer, nullable=False, index=True)
    input_path = Column(Text, nullable=False)
    parsed_path = Column(Text, nullable=False)
    download_filename = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
