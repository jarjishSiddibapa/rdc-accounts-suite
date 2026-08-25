"""Admin-only audit log viewing + automated backup management routes.

Kept as a separate router file from admin_routes.py (user management) so the
two can be edited independently without colliding.
"""

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app import auth, backup
from app.database import get_db
from app.models import AuditLog, BackupSettings, User
from app.regional import IST, to_ist_iso

router = APIRouter(prefix="/api/admin", tags=["admin"])

_BACKUP_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class BackupSettingsBody(BaseModel):
    enabled: bool
    backup_time: str
    max_backups: int
    scratch_cleanup_minutes: int

    @field_validator("backup_time")
    @classmethod
    def _validate_backup_time(cls, v: str) -> str:
        if not _BACKUP_TIME_RE.match(v):
            raise ValueError("backup_time must be in 24h HH:MM format")
        return v

    @field_validator("max_backups")
    @classmethod
    def _validate_max_backups(cls, v: int) -> int:
        if not (1 <= v <= 365):
            raise ValueError("max_backups must be between 1 and 365")
        return v

    @field_validator("scratch_cleanup_minutes")
    @classmethod
    def _validate_scratch_cleanup_minutes(cls, v: int) -> int:
        if not (5 <= v <= 43_200):
            raise ValueError("scratch_cleanup_minutes must be between 5 and 43200 (30 days)")
        return v


def _settings_dict(settings: BackupSettings) -> dict:
    return {
        "enabled": settings.enabled,
        "backup_time": settings.backup_time,
        "max_backups": settings.max_backups,
        "scratch_cleanup_minutes": settings.scratch_cleanup_minutes,
    }


def _get_or_create_settings(db: Session) -> BackupSettings:
    settings = db.query(BackupSettings).filter(BackupSettings.id == 1).first()
    if settings is None:
        settings = BackupSettings(
            id=1, enabled=True, backup_time="03:00", max_backups=30, scratch_cleanup_minutes=30,
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    elif settings.is_deleted:
        settings.is_deleted = False
        db.commit()
    return settings


def _audit_log_dict(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "timestamp": to_ist_iso(row.timestamp),
        "actor_email": row.actor_email,
        "action": row.action,
        "status_code": row.status_code,
        "ip_address": row.ip_address,
        "details": row.details,
    }


def _ist_date_to_utc_naive(value: date) -> datetime:
    """Midnight of an IST calendar date, converted to the naive UTC the
    audit_log.timestamp column actually stores (see AuditLog.timestamp's
    default=datetime.utcnow)."""
    return (
        datetime(value.year, value.month, value.day, tzinfo=IST)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


@router.get("/audit-log", dependencies=[Depends(auth.require_admin)])
def get_audit_log(
    limit: int = 100,
    offset: int = 0,
    user_id: int | None = None,
    action_contains: str | None = None,
    actor_contains: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)

    query = db.query(AuditLog).filter(AuditLog.is_deleted == False)  # noqa: E712
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if action_contains:
        query = query.filter(AuditLog.action.contains(action_contains))
    if actor_contains:
        # Also matches by first/last name, not just the stored actor_email,
        # since that's what an admin is more likely to search by.
        query = query.outerjoin(User, AuditLog.user_id == User.id).filter(
            or_(
                AuditLog.actor_email.contains(actor_contains),
                User.first_name.contains(actor_contains),
                User.last_name.contains(actor_contains),
            )
        )
    if start_date:
        try:
            query = query.filter(AuditLog.timestamp >= _ist_date_to_utc_naive(date.fromisoformat(start_date)))
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date must be an ISO date (YYYY-MM-DD)")
    if end_date:
        try:
            end_exclusive = _ist_date_to_utc_naive(date.fromisoformat(end_date) + timedelta(days=1))
        except ValueError:
            raise HTTPException(status_code=400, detail="end_date must be an ISO date (YYYY-MM-DD)")
        query = query.filter(AuditLog.timestamp < end_exclusive)

    total = query.count()
    rows = query.order_by(desc(AuditLog.timestamp)).offset(offset).limit(limit).all()

    return {"total": total, "items": [_audit_log_dict(r) for r in rows]}


@router.get("/backup-settings", dependencies=[Depends(auth.require_admin)])
def get_backup_settings(db: Session = Depends(get_db)):
    return _settings_dict(_get_or_create_settings(db))


@router.put("/backup-settings", dependencies=[Depends(auth.require_admin)])
def put_backup_settings(body: BackupSettingsBody, db: Session = Depends(get_db)):
    settings = _get_or_create_settings(db)
    settings.enabled = body.enabled
    settings.backup_time = body.backup_time
    settings.max_backups = body.max_backups
    settings.scratch_cleanup_minutes = body.scratch_cleanup_minutes
    db.commit()
    db.refresh(settings)
    return _settings_dict(settings)


@router.post("/backup-settings/run-now", dependencies=[Depends(auth.require_admin)])
def run_backup_now(db: Session = Depends(get_db)):
    settings = _get_or_create_settings(db)
    ok, message = backup.run_backup_and_prune(settings.max_backups)
    return {"ok": ok, "message": message}


@router.get("/backups", dependencies=[Depends(auth.require_admin)])
def get_backups():
    return backup.list_backups()


@router.get("/backups/{filename}/download", dependencies=[Depends(auth.require_admin)])
def download_backup(filename: str):
    # Reject anything that isn't a bare filename (path separators, "..",
    # drive letters, etc.) before it ever touches the filesystem.
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = backup.BACKUP_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Backup not found")

    return FileResponse(str(file_path), filename=filename, media_type="application/sql")
