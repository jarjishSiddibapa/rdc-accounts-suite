"""Admin-only user management routes."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth, security, system_mailer
from app.database import get_db
from app.models import Application, SystemEmailSettings, User
from app.permissions import APP_COMPANIES, APP_KEYS, APP_LABELS, REPORT_RECIPIENT_APP_KEYS, parse_allowed_apps
from app.regional import to_ist_iso
from app.services.mailer_shared import (
    get_report_recipient_defaults,
    set_report_recipient_defaults,
)
from app.validation import normalize_email, normalize_optional_name, validate_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreateUserBody(BaseModel):
    email: str
    password: str
    role: Literal["admin", "user"]
    first_name: str | None = None
    last_name: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return normalize_optional_name(value)


class UpdateEmailBody(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class UpdateProfileBody(BaseModel):
    email: str
    first_name: str | None = None
    last_name: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return normalize_optional_name(value)


class ResetPasswordBody(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)


class PermissionsBody(BaseModel):
    # Omitted means no access. Only an admin can place application keys here.
    allowed_apps: list[str] = Field(default_factory=list)


class ApplicationCompanyBody(BaseModel):
    company: Literal["RDC", "Ultrafine"]


class ApplicationCollaboratorBody(BaseModel):
    # Empty/whitespace-only clears the credit back to "Made by Jarjish" alone.
    collaborator: str | None = None

    @field_validator("collaborator")
    @classmethod
    def normalize_collaborator(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class ActiveBody(BaseModel):
    is_active: bool


class SystemEmailBody(BaseModel):
    sender_email: str
    app_password: str | None = None

    @field_validator("sender_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class SmtpTestBody(BaseModel):
    sender_email: str
    app_password: str | None = None

    @field_validator("sender_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class ReportRecipientsBody(BaseModel):
    app_key: str
    default_to: list[str]
    default_cc: list[str]

    @field_validator("app_key")
    @classmethod
    def validate_app_key(cls, value: str) -> str:
        if value not in REPORT_RECIPIENT_APP_KEYS:
            raise ValueError("This application doesn't use admin-managed default recipients")
        return value


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": user.role,
        "created_at": to_ist_iso(user.created_at),
        "is_active": user.is_active,
        "is_deleted": user.is_deleted,
        "allowed_apps": None if user.role == "admin" else parse_allowed_apps(user),
    }


def _application_dict(application: Application) -> dict:
    return {
        "key": application.key,
        "label": application.label,
        "company": application.company,
        "collaborator": application.collaborator,
    }


@router.get("/apps", dependencies=[Depends(auth.require_admin)])
def list_apps(db: Session = Depends(get_db)):
    """Return searchable application metadata for the admin control panel."""
    applications = (
        db.query(Application)
        .filter(Application.is_deleted == False)  # noqa: E712
        .order_by(Application.company, Application.label)
        .all()
    )
    return [_application_dict(application) for application in applications]


@router.put("/apps/{app_key}/company", dependencies=[Depends(auth.require_admin)])
def set_application_company(
    app_key: str,
    body: ApplicationCompanyBody,
    db: Session = Depends(get_db),
):
    """Change an application's company classification (admin only)."""
    application = (
        db.query(Application)
        .filter(Application.key == app_key, Application.is_deleted == False)  # noqa: E712
        .first()
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if body.company not in APP_COMPANIES:
        raise HTTPException(status_code=422, detail="Unknown company")
    application.company = body.company
    db.commit()
    db.refresh(application)
    return _application_dict(application)


@router.put("/apps/{app_key}/collaborator", dependencies=[Depends(auth.require_admin)])
def set_application_collaborator(
    app_key: str,
    body: ApplicationCollaboratorBody,
    db: Session = Depends(get_db),
):
    """Set (or clear) who an application's footer credits alongside Jarjish,
    who is always shown regardless of this value (admin only)."""
    application = (
        db.query(Application)
        .filter(Application.key == app_key, Application.is_deleted == False)  # noqa: E712
        .first()
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    application.collaborator = body.collaborator
    db.commit()
    db.refresh(application)
    return _application_dict(application)


@router.get("/apps/credits")
def list_application_credits(
    db: Session = Depends(get_db),
    _user: User = Depends(auth.get_current_user),
):
    """Footer data for every logged-in user (not admin-only): each active
    app's collaborator, so the footer can read 'Made by Jarjish & {name}'
    on that app's own page instead of the suite-wide default."""
    applications = (
        db.query(Application)
        .filter(Application.is_deleted == False)  # noqa: E712
        .all()
    )
    return {application.key: application.collaborator for application in applications}


@router.get("/users", dependencies=[Depends(auth.require_admin)])
def list_users(include_archived: bool = False, db: Session = Depends(get_db)):
    query = db.query(User)
    if not include_archived:
        query = query.filter(User.is_deleted == False)  # noqa: E712
    users = query.order_by(User.id).all()
    return [_user_dict(u) for u in users]


@router.post("/users", dependencies=[Depends(auth.require_admin)])
def create_user(body: CreateUserBody, db: Session = Depends(get_db)):
    email = body.email
    # Checked against ALL users, including soft-deleted ones: the email
    # column stays uniquely reserved even after a soft delete (nothing is
    # ever hard-deleted, so the row - and its email - still exists).
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A user with that email already exists")

    user = User(
        email=email,
        first_name=body.first_name,
        last_name=body.last_name,
        password_hash=auth.hash_password(body.password),
        role=body.role,
        allowed_apps=None if body.role == "admin" else json.dumps([]),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
    db.refresh(user)
    return _user_dict(user)


@router.put("/users/{user_id}/profile", dependencies=[Depends(auth.require_admin)])
def update_profile(user_id: int, body: UpdateProfileBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    duplicate = db.query(User).filter(User.email == body.email, User.id != user_id).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="A user with that email already exists")

    user.email = body.email
    user.first_name = body.first_name
    user.last_name = body.last_name
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
    db.refresh(user)
    return _user_dict(user)


@router.put("/users/{user_id}/email", dependencies=[Depends(auth.require_admin)])
def update_email(user_id: int, body: UpdateEmailBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    email = body.email
    existing = db.query(User).filter(User.email == email, User.id != user_id).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A user with that email already exists")

    user.email = email
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
    db.refresh(user)
    return _user_dict(user)


@router.post("/users/{user_id}/reset-password", dependencies=[Depends(auth.require_admin)])
def reset_password(user_id: int, body: ResetPasswordBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = auth.hash_password(body.password)
    db.commit()
    return {"ok": True}


@router.get("/system-email", dependencies=[Depends(auth.require_admin)])
def get_system_email(db: Session = Depends(get_db)):
    """The application's own sender identity (password-reset / notification
    emails) - distinct from each user's personal report-sending identity."""
    settings = system_mailer.get_system_email_settings(db)
    return {"sender_email": settings["sender_email"], "configured": settings["configured"]}


@router.put("/system-email", dependencies=[Depends(auth.require_admin)])
def put_system_email(body: SystemEmailBody, db: Session = Depends(get_db)):
    row = db.query(SystemEmailSettings).filter(SystemEmailSettings.id == 1).first()
    if row is None:
        row = SystemEmailSettings(id=1)
        db.add(row)

    row.sender_email = body.sender_email
    row.is_deleted = False
    if body.app_password:
        row.app_password_encrypted = security.encrypt(body.app_password)
    elif not row.app_password_encrypted:
        raise HTTPException(status_code=422, detail="An app password is required for initial setup")
    db.commit()
    return {
        "sender_email": row.sender_email,
        "configured": bool(row.app_password_encrypted),
    }


@router.post("/system-email/test", dependencies=[Depends(auth.require_admin)])
def test_system_email(body: SmtpTestBody, db: Session = Depends(get_db)):
    password = body.app_password
    if not password:
        existing = system_mailer.get_system_email_settings(db)
        if not existing["configured"] or existing["sender_email"] != body.sender_email:
            raise HTTPException(
                status_code=422,
                detail="Enter an app password when testing a new sender email",
            )
        password = existing["app_password"]
    ok, message = system_mailer.test_smtp_connection(body.sender_email, password)
    return {"ok": ok, "message": message}


@router.get("/report-recipients", dependencies=[Depends(auth.require_admin)])
def get_report_recipients(db: Session = Depends(get_db)):
    """Return admin-managed default recipients — only for applications that
    actually send mail using an application-wide default To/Cc (see
    REPORT_RECIPIENT_APP_KEYS). Apps with no email feature at all, and bulk
    per-customer senders whose recipients always come from that customer's
    own saved mapping/upload, are deliberately excluded from this list."""
    return {
        "applications": [
            {
                "key": key,
                "label": APP_LABELS[key],
                **get_report_recipient_defaults(db, key),
            }
            for key in APP_KEYS
            if key in REPORT_RECIPIENT_APP_KEYS
        ]
    }


@router.put("/report-recipients", dependencies=[Depends(auth.require_admin)])
def put_report_recipients(body: ReportRecipientsBody, db: Session = Depends(get_db)):
    try:
        result = set_report_recipient_defaults(
            db,
            body.app_key,
            body.default_to,
            body.default_cc,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Each default recipient email must be unique within an application",
        ) from exc
    return {"app_key": body.app_key, **result}


@router.put("/users/{user_id}/permissions", dependencies=[Depends(auth.require_admin)])
def set_permissions(user_id: int, body: PermissionsBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin":
        raise HTTPException(
            status_code=400,
            detail="Admins always have full access to every application - restrictions only apply to regular users.",
        )

    unknown = [k for k in body.allowed_apps if k not in APP_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown app key(s): {unknown}")
    # Deduplicate while preserving the catalogue order so stored permissions
    # remain deterministic and easy to audit.
    selected = set(body.allowed_apps)
    user.allowed_apps = json.dumps([key for key in APP_KEYS if key in selected])

    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.put("/users/{user_id}/active", dependencies=[Depends(auth.require_admin)])
def set_user_active(user_id: int, body: ActiveBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "admin" and not body.is_active:
        active_admin_count = (
            db.query(User)
            .filter(User.role == "admin", User.is_deleted == False, User.is_active == True)  # noqa: E712
            .count()
        )
        if active_admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot deactivate the last active admin")

    user.is_active = body.is_active
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.delete("/users/{user_id}", dependencies=[Depends(auth.require_admin)])
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Soft delete only - nothing is ever removed from the database. The
    row (and its now-logged-out session, if any) stays forever, just
    excluded from listings and login."""
    user = db.query(User).filter(User.id == user_id, User.is_deleted == False).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user.role == "admin" and user.is_active:
        active_admin_count = (
            db.query(User)
            .filter(
                User.role == "admin",
                User.is_deleted == False,  # noqa: E712
                User.is_active == True,  # noqa: E712
            )
            .count()
        )
        if active_admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot archive the last active admin")

    user.is_deleted = True
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/restore", dependencies=[Depends(auth.require_admin)])
def restore_user(user_id: int, db: Session = Depends(get_db)):
    """Restore a soft-deleted account as inactive so an admin explicitly
    reviews its access before allowing sign-in again."""
    user = db.query(User).filter(User.id == user_id, User.is_deleted == True).first()  # noqa: E712
    if user is None:
        raise HTTPException(status_code=404, detail="Archived user not found")
    user.is_deleted = False
    user.is_active = False
    if user.role != "admin":
        user.allowed_apps = json.dumps([])
    db.commit()
    db.refresh(user)
    return _user_dict(user)
