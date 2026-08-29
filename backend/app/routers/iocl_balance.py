"""IOCL CCMS balance monitor configuration, status, and manual check routes."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app import security
from app.auth import get_current_user, require_admin
from app.database import get_db
from app.jobs import cancel_job, get_job, submit_job
from app.models import IoclBalanceCheck, IoclBalanceNotification, IoclBalanceSettings, User
from app.permissions import require_app_access
from app.regional import to_ist_iso
from app.services.iocl_balance import monitor
from app.services.mailer_shared import send_mail

router = APIRouter(
    prefix="/api/tools/iocl-balance",
    tags=["iocl-balance"],
    dependencies=[Depends(require_app_access("iocl-balance-monitor"))],
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SettingsBody(BaseModel):
    version: int
    enabled: bool
    login_url: str = Field(min_length=8, max_length=500)
    username: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, max_length=500)
    sender_email: EmailStr | None = None
    sender_app_password: SecretStr | None = Field(default=None, max_length=500)
    login_timeout_seconds: int = Field(ge=15, le=300)
    check_interval_minutes: int = Field(ge=5, le=1440)
    daily_email_enabled: bool
    daily_email_time: str
    daily_to: list[EmailStr]
    daily_cc: list[EmailStr]
    daily_subject_template: str = Field(min_length=1, max_length=1000)
    daily_body_template: str = Field(min_length=1, max_length=20_000)
    alerts_enabled: bool
    alert_start_amount: Decimal = Field(ge=0, le=Decimal("1000000000"))
    alert_repeat_hours: int = Field(ge=1, le=720)
    alert_to: list[EmailStr]
    alert_cc: list[EmailStr]
    alert_subject_template: str = Field(min_length=1, max_length=1000)
    alert_body_template: str = Field(min_length=1, max_length=20_000)

    @field_validator("daily_email_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError("Time must be in 24-hour HH:MM format")
        return value

    @field_validator("login_url")
    @classmethod
    def validate_login_url(cls, value: str) -> str:
        value = value.strip()
        if not value.lower().startswith("https://"):
            raise ValueError("The IOCL login URL must use HTTPS")
        return value

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip()

    @field_validator("daily_to", "daily_cc", "alert_to", "alert_cc")
    @classmethod
    def deduplicate_recipients(cls, value: list[EmailStr]) -> list[EmailStr]:
        return list(dict.fromkeys(value))


def _status_dict(row: IoclBalanceSettings) -> dict:
    portal_configured = bool(row.username and row.password_encrypted)
    sender_configured = bool(row.sender_email and row.sender_app_password_encrypted)
    return {
        "enabled": row.enabled,
        "portal_configured": portal_configured,
        "mail_configured": sender_configured,
        "check_interval_minutes": row.check_interval_minutes,
        "last_balance": float(row.last_balance) if row.last_balance is not None else None,
        "last_checked_at": to_ist_iso(row.last_checked_at),
        "last_check_status": row.last_check_status,
        "last_error": row.last_error,
        "next_check_at": to_ist_iso(row.next_check_at),
    }


def _settings_dict(row: IoclBalanceSettings) -> dict:
    return {
        **_status_dict(row),
        "version": row.version,
        "sender_email": row.sender_email or "",
        "sender_app_password_configured": bool(row.sender_app_password_encrypted),
        "login_url": row.login_url,
        "username": row.username or "",
        "password_configured": bool(row.password_encrypted),
        "session_configured": bool(row.session_state_encrypted),
        "login_timeout_seconds": row.login_timeout_seconds,
        "check_interval_minutes": row.check_interval_minutes,
        "daily_email_enabled": row.daily_email_enabled,
        "daily_email_time": row.daily_email_time,
        "daily_to": monitor.parse_recipients(row.daily_to),
        "daily_cc": monitor.parse_recipients(row.daily_cc),
        "daily_subject_template": row.daily_subject_template,
        "daily_body_template": row.daily_body_template,
        "alerts_enabled": row.alerts_enabled,
        "alert_start_amount": float(row.alert_start_amount),
        "alert_repeat_hours": row.alert_repeat_hours,
        "alert_to": monitor.parse_recipients(row.alert_to),
        "alert_cc": monitor.parse_recipients(row.alert_cc),
        "alert_subject_template": row.alert_subject_template,
        "alert_body_template": row.alert_body_template,
        "last_daily_sent_date": row.last_daily_sent_date.isoformat() if row.last_daily_sent_date else None,
    }


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    """Safe monitoring summary for every user assigned to this application."""
    return _status_dict(monitor.get_or_create_settings(db))


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings(db: Session = Depends(get_db)):
    return _settings_dict(monitor.get_or_create_settings(db))


@router.put("/settings", dependencies=[Depends(require_admin)])
def put_settings(
    body: SettingsBody,
    db: Session = Depends(get_db),
):
    row = db.query(IoclBalanceSettings).filter(IoclBalanceSettings.id == 1).with_for_update().first()
    if row is None:
        monitor.seed_settings(db)
        row = db.query(IoclBalanceSettings).filter_by(id=1).with_for_update().one()
    if body.version != row.version:
        raise HTTPException(
            status_code=409,
            detail="These settings were changed in another tab. Reload before saving again.",
        )
    if body.enabled and (not body.username or (not body.password and not row.password_encrypted)):
        raise HTTPException(status_code=400, detail="Username and password are required before enabling monitoring")
    sender_email = str(body.sender_email).strip().lower() if body.sender_email else ""
    sender_identity_changed = (row.sender_email or "").lower() != sender_email
    sender_password_configured = bool(
        body.sender_app_password
        or (not sender_identity_changed and row.sender_app_password_encrypted)
    )
    if body.enabled and (body.daily_email_enabled or body.alerts_enabled) and not (
        sender_email and sender_password_configured
    ):
        raise HTTPException(
            status_code=400,
            detail="Configure the IOCL sender email and app password before enabling email automation",
        )
    if body.enabled and body.daily_email_enabled and not body.daily_to:
        raise HTTPException(status_code=400, detail="At least one morning-mail To recipient is required")
    if body.enabled and body.alerts_enabled and not body.alert_to:
        raise HTTPException(status_code=400, detail="At least one alert-mail To recipient is required")
    try:
        monitor.validate_template(body.daily_subject_template, monitor.DAILY_TEMPLATE_FIELDS)
        monitor.validate_template(body.daily_body_template, monitor.DAILY_TEMPLATE_FIELDS)
        monitor.validate_template(body.alert_subject_template, monitor.ALERT_TEMPLATE_FIELDS)
        monitor.validate_template(body.alert_body_template, monitor.ALERT_TEMPLATE_FIELDS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_identity_changed = row.username != body.username or row.login_url != body.login_url
    row.enabled = body.enabled
    row.sender_email = sender_email or None
    if body.sender_app_password:
        row.sender_app_password_encrypted = security.encrypt(
            body.sender_app_password.get_secret_value()
        )
    elif sender_identity_changed:
        # An app password belongs to the sender account. Never retain one
        # silently after the administrator changes the sender address.
        row.sender_app_password_encrypted = None
    row.sender_user_id = None
    row.login_url = body.login_url
    row.username = body.username or None
    if body.password:
        row.password_encrypted = security.encrypt(body.password)
        session_identity_changed = True
    if session_identity_changed:
        row.session_state_encrypted = None
    row.login_timeout_seconds = body.login_timeout_seconds
    row.check_interval_minutes = body.check_interval_minutes
    row.daily_email_enabled = body.daily_email_enabled
    row.daily_email_time = body.daily_email_time
    row.daily_to = json.dumps([str(value) for value in body.daily_to])
    row.daily_cc = json.dumps([str(value) for value in body.daily_cc])
    row.daily_subject_template = body.daily_subject_template
    row.daily_body_template = body.daily_body_template
    row.alerts_enabled = body.alerts_enabled
    row.alert_start_amount = body.alert_start_amount
    row.alert_repeat_hours = body.alert_repeat_hours
    row.alert_to = json.dumps([str(value) for value in body.alert_to])
    row.alert_cc = json.dumps([str(value) for value in body.alert_cc])
    row.alert_subject_template = body.alert_subject_template
    row.alert_body_template = body.alert_body_template
    row.next_check_at = _utcnow() if body.enabled else None
    row.updated_at = _utcnow()
    row.version += 1
    row.is_deleted = False
    db.commit()
    db.refresh(row)
    return _settings_dict(row)


class TestMailBody(BaseModel):
    mail_type: Literal["daily", "alert"]
    subject_template: str = Field(min_length=1, max_length=1000)
    body_template: str = Field(min_length=1, max_length=20_000)


@router.post("/test-mail", dependencies=[Depends(require_admin)])
def send_test_mail(
    body: TestMailBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Renders the given (possibly unsaved) template with sample data and
    sends it to the current administrator's own inbox only - never to the real
    To/Cc list - so the administrator can see exactly how the mail
    will look before saving."""
    fields = monitor.DAILY_TEMPLATE_FIELDS if body.mail_type == "daily" else monitor.ALERT_TEMPLATE_FIELDS
    try:
        monitor.validate_template(body.subject_template, fields)
        monitor.validate_template(body.body_template, fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = monitor.get_or_create_settings(db)
    if not settings.sender_email or not settings.sender_app_password_encrypted:
        raise HTTPException(
            status_code=400,
            detail="Configure the IOCL sender email and app password before sending a test mail",
        )

    sample_balance = Decimal(str(settings.last_balance)) if settings.last_balance is not None else Decimal("1245620.60")
    sample_threshold = settings.alert_start_amount if body.mail_type == "alert" else None
    subject, rendered_body = monitor.render_preview(body.subject_template, body.body_template, sample_balance, sample_threshold)

    try:
        send_mail(
            from_email=settings.sender_email,
            app_password=security.decrypt(settings.sender_app_password_encrypted),
            to_addresses=[current_user.email],
            cc_addresses=[],
            subject=f"[Test] {subject}",
            html_body=rendered_body.replace("\n", "<br>"),
            attachments=[],
        )
    except Exception as exc:  # noqa: BLE001 - report the real SMTP failure back to the user
        raise HTTPException(status_code=502, detail=f"Could not send the test mail: {exc}") from exc

    return {"ok": True, "sent_to": current_user.email}


@router.post("/session", dependencies=[Depends(require_admin)])
async def import_session(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw = await file.read(256_001)
    if len(raw) > 256_000:
        raise HTTPException(status_code=413, detail="The browser session file is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Select a valid Playwright session JSON file") from exc
    if not isinstance(value, dict) or not isinstance(value.get("cookies", []), list):
        raise HTTPException(status_code=400, detail="The session JSON structure is invalid")
    row = monitor.get_or_create_settings(db)
    row.session_state_encrypted = security.encrypt(json.dumps(value, separators=(",", ":")))
    row.updated_at = _utcnow()
    row.version += 1
    db.commit()
    db.refresh(row)
    return _settings_dict(row)


@router.post("/session/clear", dependencies=[Depends(require_admin)])
def clear_session(db: Session = Depends(get_db)):
    row = monitor.get_or_create_settings(db)
    row.session_state_encrypted = None
    row.updated_at = _utcnow()
    row.version += 1
    db.commit()
    db.refresh(row)
    return _settings_dict(row)


@router.post("/check-now")
def check_now(user: User = Depends(get_current_user)):
    job_id = submit_job(
        monitor.run_check_job,
        "manual",
        owner_id=user.id,
        cancel_on_disconnect=False,
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user: User = Depends(get_current_user)):
    job = cancel_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/checks")
def get_checks(
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    trigger: str | None = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    query = db.query(IoclBalanceCheck).filter(IoclBalanceCheck.is_deleted.is_(False))
    if status in {"success", "error", "skipped"}:
        query = query.filter(IoclBalanceCheck.status == status)
    if trigger in {"scheduled", "manual"}:
        query = query.filter(IoclBalanceCheck.trigger == trigger)
    total = query.count()
    rows = query.order_by(desc(IoclBalanceCheck.checked_at)).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "trigger": row.trigger,
                "status": row.status,
                "balance": float(row.balance) if row.balance is not None else None,
                "error_message": row.error_message,
                "checked_at": to_ist_iso(row.checked_at),
                "duration_seconds": row.duration_seconds,
            }
            for row in rows
        ],
    }


@router.get("/notifications")
def get_notifications(
    limit: int = 20,
    offset: int = 0,
    notification_type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    query = db.query(IoclBalanceNotification).filter(IoclBalanceNotification.is_deleted.is_(False))
    if notification_type in {"daily", "threshold"}:
        query = query.filter(IoclBalanceNotification.notification_type == notification_type)
    if status in {"pending", "sending", "sent", "failed"}:
        query = query.filter(IoclBalanceNotification.status == status)
    total = query.count()
    rows = query.order_by(desc(IoclBalanceNotification.created_at)).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "notification_type": row.notification_type,
                "threshold_amount": float(row.threshold_amount) if row.threshold_amount is not None else None,
                "balance": float(row.balance),
                "subject": row.subject,
                "status": row.status,
                "error_message": row.error_message,
                "created_at": to_ist_iso(row.created_at),
                "sent_at": to_ist_iso(row.sent_at),
            }
            for row in rows
        ],
    }
