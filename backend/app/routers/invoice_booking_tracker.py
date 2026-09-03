"""Ultrafine Invoice Booking Tracker configuration, scans, and history."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app import security
from app.auth import get_current_user, require_admin
from app.database import get_db
from app.jobs import cancel_job, get_job, submit_job
from app.models import (
    InvoiceBookingTrackerCheck,
    InvoiceBookingTrackerMapping,
    InvoiceBookingTrackerNotification,
    InvoiceBookingTrackerSettings,
    User,
)
from app.permissions import require_app_access
from app.public_messages import PUBLIC_ISSUE_MESSAGE
from app.regional import to_ist_iso
from app.services.invoice_booking_tracker import monitor
from app.services.mailer_shared import send_mail

router = APIRouter(
    prefix="/api/tools/invoice-booking-tracker",
    tags=["invoice-booking-tracker"],
    dependencies=[Depends(require_app_access(monitor.APP_KEY))],
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _is_complete_tracker_rows(value) -> bool:
    if not isinstance(value, list) or not value:
        return False
    required_counts = ("pending", "records_scanned", "pages_scanned")
    return all(
        isinstance(item, dict)
        and isinstance(item.get("location"), str)
        and bool(item["location"].strip())
        and isinstance(item.get("responsible_person"), str)
        and all(
            isinstance(item.get(field), int)
            and not isinstance(item.get(field), bool)
            and item[field] >= 0
            for field in required_counts
        )
        for item in value
    )


class SettingsBody(BaseModel):
    version: int
    enabled: bool
    login_url: str = Field(min_length=8, max_length=500)
    username: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, max_length=500)
    login_timeout_seconds: int = Field(ge=15, le=300)
    sender_email: EmailStr | None = None
    sender_app_password: SecretStr | None = Field(default=None, max_length=500)
    scheduled_email_enabled: bool
    scheduled_email_time: str
    mail_to: list[EmailStr]
    mail_cc: list[EmailStr]
    subject_template: str = Field(min_length=1, max_length=1000)
    body_template: str = Field(min_length=1, max_length=20_000)
    signature: str = Field(default="", max_length=5000)

    @field_validator("login_url")
    @classmethod
    def secure_url(cls, value: str) -> str:
        value = value.strip()
        if not value.casefold().startswith("https://"):
            raise ValueError("The DMS login URL must use HTTPS")
        return value

    @field_validator("scheduled_email_time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError("Time must be in 24-hour HH:MM format")
        return value

    @field_validator("mail_to", "mail_cc")
    @classmethod
    def unique_recipients(cls, values):
        return list(dict.fromkeys(values))


class MappingBody(BaseModel):
    location: str = Field(min_length=1, max_length=255)
    responsible_person: str = Field(min_length=1, max_length=255)
    queue_label: str = Field(min_length=1, max_length=500)
    queue_key: str | None = Field(default=None, max_length=500)
    sort_order: int = Field(ge=0, le=100_000)
    is_active: bool = True

    @field_validator("location", "responsible_person", "queue_label")
    @classmethod
    def trim_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("queue_key")
    @classmethod
    def trim_optional(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class TestMailBody(BaseModel):
    subject_template: str = Field(min_length=1, max_length=1000)
    body_template: str = Field(min_length=1, max_length=20_000)


def _visible_error(value: str | None, admin: bool) -> str | None:
    if not value or admin:
        return value
    if monitor.ACCOUNT_IN_USE_ERROR_PREFIX in value:
        return monitor.ACCOUNT_IN_USE_PUBLIC_MESSAGE
    return PUBLIC_ISSUE_MESSAGE


def _status(row: InvoiceBookingTrackerSettings, admin: bool) -> dict:
    return {
        "enabled": row.enabled,
        "portal_configured": bool(row.username and row.password_encrypted),
        "mail_configured": bool(row.sender_email and row.sender_app_password_encrypted),
        "scheduled_email_enabled": row.scheduled_email_enabled,
        "scheduled_email_time": row.scheduled_email_time,
        "last_total_pending": row.last_total_pending,
        "last_checked_at": to_ist_iso(row.last_checked_at),
        "last_check_status": row.last_check_status,
        "last_error": _visible_error(row.last_error, admin),
        "last_scheduled_sent_date": row.last_scheduled_sent_date.isoformat() if row.last_scheduled_sent_date else None,
    }


def _settings(row: InvoiceBookingTrackerSettings) -> dict:
    return {
        **_status(row, True),
        "version": row.version,
        "login_url": row.login_url,
        "username": row.username or "",
        "password_configured": bool(row.password_encrypted),
        "session_configured": bool(row.session_state_encrypted),
        "login_timeout_seconds": row.login_timeout_seconds,
        "sender_email": row.sender_email or "",
        "sender_app_password_configured": bool(row.sender_app_password_encrypted),
        "mail_to": monitor.parse_recipients(row.mail_to),
        "mail_cc": monitor.parse_recipients(row.mail_cc),
        "subject_template": row.subject_template,
        "body_template": row.body_template,
        "signature": row.signature or "",
    }


def _mapping(row: InvoiceBookingTrackerMapping) -> dict:
    return {
        "id": row.id,
        "location": row.location,
        "responsible_person": row.responsible_person,
        "queue_label": row.queue_label,
        "queue_key": row.queue_key or "",
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "is_deleted": row.is_deleted,
    }


@router.get("/status")
def get_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _status(monitor.get_or_create_settings(db), user.role == "admin")


def _latest_complete_check(db: Session) -> tuple[InvoiceBookingTrackerCheck | None, list[dict] | None]:
    """The newest successful, non-deleted check whose stored rows are complete."""
    candidates = (
        db.query(InvoiceBookingTrackerCheck)
        .filter(
            InvoiceBookingTrackerCheck.is_deleted.is_(False),
            InvoiceBookingTrackerCheck.status == "success",
            InvoiceBookingTrackerCheck.result_json.isnot(None),
        )
        .order_by(desc(InvoiceBookingTrackerCheck.checked_at), desc(InvoiceBookingTrackerCheck.id))
        .yield_per(20)
    )
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.result_json or "")
        except (TypeError, ValueError):
            continue
        if _is_complete_tracker_rows(parsed):
            return candidate, parsed
    return None, None


@router.get("/latest")
def latest_tracker(db: Session = Depends(get_db)):
    """Return the newest complete snapshot used by the tracker/mail workflow."""
    row, rows = _latest_complete_check(db)
    if row is None or rows is None:
        return {
            "available": False,
            "check_id": None,
            "trigger": None,
            "checked_at": None,
            "total_pending": None,
            "total_records_scanned": None,
            "total_pages_scanned": None,
            "rows": [],
        }
    return {
        "available": True,
        "check_id": row.id,
        "trigger": row.trigger,
        "checked_at": to_ist_iso(row.checked_at),
        "total_pending": row.total_pending,
        "total_records_scanned": row.total_records_scanned,
        "total_pages_scanned": row.total_pages_scanned,
        "rows": rows,
    }


@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings(db: Session = Depends(get_db)):
    return _settings(monitor.get_or_create_settings(db))


@router.put("/settings", dependencies=[Depends(require_admin)])
def put_settings(body: SettingsBody, db: Session = Depends(get_db)):
    row = db.query(InvoiceBookingTrackerSettings).filter_by(id=1).with_for_update().first()
    if row is None:
        monitor.seed_settings(db)
        row = db.query(InvoiceBookingTrackerSettings).filter_by(id=1).with_for_update().one()
    if body.version != row.version:
        raise HTTPException(409, "These settings were changed in another tab. Reload before saving again.")
    if body.enabled and (not body.username or (not body.password and not row.password_encrypted)):
        raise HTTPException(400, "Configure the DMS username and password before enabling automation")
    sender = str(body.sender_email).strip().casefold() if body.sender_email else ""
    sender_changed = (row.sender_email or "").casefold() != sender
    has_sender_password = bool(body.sender_app_password or (not sender_changed and row.sender_app_password_encrypted))
    if body.enabled and body.scheduled_email_enabled and not (sender and has_sender_password):
        raise HTTPException(400, "Configure the dedicated sender email and app password before enabling scheduled mail")
    if body.enabled and body.scheduled_email_enabled and not body.mail_to:
        raise HTTPException(400, "At least one scheduled-mail To recipient is required")
    try:
        monitor.validate_template(body.subject_template, allow_table=False)
        monitor.validate_template(body.body_template)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    portal_changed = row.login_url != body.login_url or row.username != body.username.strip()
    row.enabled = body.enabled
    row.login_url = body.login_url
    row.username = body.username.strip() or None
    if body.password:
        row.password_encrypted = security.encrypt(body.password)
        portal_changed = True
    if portal_changed:
        row.session_state_encrypted = None
    row.login_timeout_seconds = body.login_timeout_seconds
    row.sender_email = sender or None
    if body.sender_app_password:
        row.sender_app_password_encrypted = security.encrypt(body.sender_app_password.get_secret_value())
    elif sender_changed:
        row.sender_app_password_encrypted = None
    row.scheduled_email_enabled = body.scheduled_email_enabled
    row.scheduled_email_time = body.scheduled_email_time
    row.mail_to = json.dumps([str(value) for value in body.mail_to])
    row.mail_cc = json.dumps([str(value) for value in body.mail_cc])
    row.subject_template = body.subject_template
    row.body_template = body.body_template
    row.signature = body.signature.strip() or None
    row.updated_at = _utcnow()
    row.version += 1
    row.is_deleted = False
    db.commit()
    db.refresh(row)
    return _settings(row)


@router.post("/session", dependencies=[Depends(require_admin)])
async def import_session(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read(256_001)
    if len(raw) > 256_000:
        raise HTTPException(413, "The browser session file is too large")
    try:
        state = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "Select a valid Playwright session JSON file") from exc
    if not isinstance(state, dict) or not isinstance(state.get("cookies", []), list):
        raise HTTPException(400, "The session JSON structure is invalid")
    row = monitor.get_or_create_settings(db)
    row.session_state_encrypted = security.encrypt(json.dumps(state, separators=(",", ":")))
    row.version += 1
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _settings(row)


@router.post("/session/clear", dependencies=[Depends(require_admin)])
def clear_session(db: Session = Depends(get_db)):
    row = monitor.get_or_create_settings(db)
    row.session_state_encrypted = None
    row.version += 1
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _settings(row)


@router.post("/test-mail", dependencies=[Depends(require_admin)])
def test_mail(body: TestMailBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        monitor.validate_template(body.subject_template, allow_table=False)
        monitor.validate_template(body.body_template)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    settings = monitor.get_or_create_settings(db)
    if not settings.sender_email or not settings.sender_app_password_encrypted:
        raise HTTPException(400, "Configure the dedicated sender before sending a test mail")
    _, rows = _latest_complete_check(db)
    if not rows:
        # No real check has ever succeeded yet - show placeholder numbers
        # purely so the template's layout can still be previewed.
        rows = [
            {"location": row.location, "responsible_person": row.responsible_person, "pending": index % 4}
            for index, row in enumerate(db.query(InvoiceBookingTrackerMapping).filter(InvoiceBookingTrackerMapping.is_deleted.is_(False), InvoiceBookingTrackerMapping.is_active.is_(True)).order_by(InvoiceBookingTrackerMapping.sort_order).all(), 1)
        ]
    subject, html_body = monitor.render_templates(body.subject_template, body.body_template, rows, signature=settings.signature)
    try:
        send_mail(from_email=settings.sender_email, app_password=security.decrypt(settings.sender_app_password_encrypted), to_addresses=[user.email], cc_addresses=[], subject=f"[Test] {subject}", html_body=html_body, attachments=[])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not send the test mail: {exc}") from exc
    return {"ok": True, "sent_to": user.email}


@router.post("/check-now")
def check_now(user: User = Depends(get_current_user)):
    return {"job_id": submit_job(monitor.run_check_job, "manual", owner_id=user.id, cancel_on_disconnect=False)}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if user.role != "admin" and job.get("status") == "error":
        error = str(job.get("error") or "")
        return {
            **job,
            "error": monitor.ACCOUNT_IN_USE_PUBLIC_MESSAGE
            if monitor.ACCOUNT_IN_USE_ERROR_PREFIX in error
            else PUBLIC_ISSUE_MESSAGE,
        }
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user: User = Depends(get_current_user)):
    job = cancel_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if user.role == "admin" or job.get("status") != "error":
        return job
    error = str(job.get("error") or "")
    return {**job, "error": monitor.ACCOUNT_IN_USE_PUBLIC_MESSAGE if monitor.ACCOUNT_IN_USE_ERROR_PREFIX in error else PUBLIC_ISSUE_MESSAGE}


@router.get("/mappings")
def mappings(search: str = "", limit: int = 25, offset: int = 0, archived: bool = False, db: Session = Depends(get_db)):
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    query = db.query(InvoiceBookingTrackerMapping).filter(InvoiceBookingTrackerMapping.is_deleted.is_(archived))
    if search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(InvoiceBookingTrackerMapping.location.like(term), InvoiceBookingTrackerMapping.responsible_person.like(term), InvoiceBookingTrackerMapping.queue_label.like(term), InvoiceBookingTrackerMapping.queue_key.like(term)))
    total = query.count()
    rows = query.order_by(InvoiceBookingTrackerMapping.sort_order, InvoiceBookingTrackerMapping.id).offset(offset).limit(limit).all()
    return {"total": total, "items": [_mapping(row) for row in rows]}


@router.post("/mappings", dependencies=[Depends(require_admin)])
def add_mapping(body: MappingBody, db: Session = Depends(get_db)):
    key = monitor.normalize_key(body.location)
    if db.query(InvoiceBookingTrackerMapping).filter_by(location_key=key).first():
        raise HTTPException(409, "A mapping for this location already exists, including archived mappings")
    row = InvoiceBookingTrackerMapping(location_key=key, **body.model_dump(), updated_at=_utcnow(), is_deleted=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _mapping(row)


@router.put("/mappings/{mapping_id}", dependencies=[Depends(require_admin)])
def update_mapping(mapping_id: int, body: MappingBody, db: Session = Depends(get_db)):
    row = db.query(InvoiceBookingTrackerMapping).filter_by(id=mapping_id).first()
    if row is None or row.is_deleted:
        raise HTTPException(404, "Mapping not found")
    key = monitor.normalize_key(body.location)
    duplicate = db.query(InvoiceBookingTrackerMapping).filter(InvoiceBookingTrackerMapping.location_key == key, InvoiceBookingTrackerMapping.id != mapping_id).first()
    if duplicate:
        raise HTTPException(409, "A mapping for this location already exists")
    row.location_key = key
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _mapping(row)


@router.delete("/mappings/{mapping_id}", dependencies=[Depends(require_admin)])
def archive_mapping(mapping_id: int, db: Session = Depends(get_db)):
    row = db.query(InvoiceBookingTrackerMapping).filter_by(id=mapping_id).first()
    if row is None or row.is_deleted:
        raise HTTPException(404, "Mapping not found")
    row.is_deleted = True
    row.is_active = False
    row.updated_at = _utcnow()
    db.commit()
    return {"ok": True}


@router.post("/mappings/{mapping_id}/restore", dependencies=[Depends(require_admin)])
def restore_mapping(mapping_id: int, db: Session = Depends(get_db)):
    row = db.query(InvoiceBookingTrackerMapping).filter_by(id=mapping_id).first()
    if row is None or not row.is_deleted:
        raise HTTPException(404, "Archived mapping not found")
    row.is_deleted = False
    row.is_active = True
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _mapping(row)


@router.get("/checks")
def checks(limit: int = 20, offset: int = 0, status: str | None = None, trigger: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    query = db.query(InvoiceBookingTrackerCheck).filter(InvoiceBookingTrackerCheck.is_deleted.is_(False))
    if status in {"success", "error", "skipped"}:
        query = query.filter(InvoiceBookingTrackerCheck.status == status)
    if trigger in {"scheduled", "manual"}:
        query = query.filter(InvoiceBookingTrackerCheck.trigger == trigger)
    total = query.count()
    rows = query.order_by(desc(InvoiceBookingTrackerCheck.checked_at)).offset(offset).limit(limit).all()
    return {"total": total, "items": [{"id": row.id, "trigger": row.trigger, "status": row.status, "total_pending": row.total_pending, "total_records_scanned": row.total_records_scanned, "total_pages_scanned": row.total_pages_scanned, "rows": json.loads(row.result_json) if row.result_json else [], "error_message": _visible_error(row.error_message, user.role == "admin"), "checked_at": to_ist_iso(row.checked_at), "duration_seconds": row.duration_seconds} for row in rows]}


@router.get("/notifications")
def notifications(limit: int = 20, offset: int = 0, status: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    query = db.query(InvoiceBookingTrackerNotification).filter(InvoiceBookingTrackerNotification.is_deleted.is_(False))
    if status in {"pending", "sending", "sent", "failed"}:
        query = query.filter(InvoiceBookingTrackerNotification.status == status)
    total = query.count()
    rows = query.order_by(desc(InvoiceBookingTrackerNotification.created_at)).offset(offset).limit(limit).all()
    return {"total": total, "items": [{"id": row.id, "check_id": row.check_id, "subject": row.subject, "attachment_filename": row.attachment_filename, "status": row.status, "error_message": _visible_error(row.error_message, user.role == "admin"), "created_at": to_ist_iso(row.created_at), "sent_at": to_ist_iso(row.sent_at)} for row in rows]}
