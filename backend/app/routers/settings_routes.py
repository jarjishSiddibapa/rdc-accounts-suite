"""Per-user email settings (SMTP sender identity + signature).

Who reports get sent TO (default recipients) is an application-wide admin
setting, not a per-user preference - see admin_routes.py's
/api/admin/report-recipients."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app import auth, security
from app.database import get_db
from app.models import EmailSettings, User
from app.services.mailer_shared import test_smtp_connection
from app.validation import normalize_email, normalize_optional_name

router = APIRouter(prefix="/api/settings", tags=["settings"])


class EmailSettingsBody(BaseModel):
    sender_email: str | None = None
    app_password: str | None = None
    signature: str | None = None

    @field_validator("sender_email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return normalize_email(value) if value else None


class SmtpTestBody(BaseModel):
    sender_email: str
    app_password: str

    @field_validator("sender_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class ProfileBody(BaseModel):
    first_name: str | None = None
    last_name: str | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return normalize_optional_name(value)


def _serialize(settings: EmailSettings | None) -> dict:
    if settings is None:
        return {
            "sender_email": None,
            "signature": None,
            "configured": False,
        }

    return {
        "sender_email": settings.sender_email,
        "signature": settings.signature,
        "configured": bool(settings.app_password_encrypted),
    }


@router.put("/profile")
def put_profile(
    body: ProfileBody,
    db: Session = Depends(get_db),
    user: User = Depends(auth.get_current_user),
):
    user.first_name = body.first_name
    user.last_name = body.last_name
    db.commit()
    db.refresh(user)
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


@router.get("/email")
def get_email_settings(
    db: Session = Depends(get_db),
    user: User = Depends(auth.get_current_user),
):
    settings = db.query(EmailSettings).filter(
        EmailSettings.user_id == user.id,
        EmailSettings.is_deleted == False,  # noqa: E712
    ).first()
    return _serialize(settings)


@router.put("/email")
def put_email_settings(
    body: EmailSettingsBody,
    db: Session = Depends(get_db),
    user: User = Depends(auth.get_current_user),
):
    settings = db.query(EmailSettings).filter(EmailSettings.user_id == user.id).first()
    if settings is None:
        settings = EmailSettings(user_id=user.id)
        db.add(settings)

    settings.sender_email = body.sender_email
    settings.is_deleted = False
    if body.app_password is not None:
        settings.app_password_encrypted = security.encrypt(body.app_password)
    settings.signature = body.signature

    db.commit()
    db.refresh(settings)
    return _serialize(settings)


@router.post("/email/test")
def test_email_settings(
    body: SmtpTestBody,
    user: User = Depends(auth.get_current_user),
):
    ok, message = test_smtp_connection(body.sender_email, body.app_password)
    return {"ok": ok, "message": message}
