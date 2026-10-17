"""Per-user email settings (SMTP sender identity + signature).

Who reports get sent TO (default recipients) is an application-wide admin
setting, not a per-user preference - see admin_routes.py's
/api/admin/report-recipients."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app import auth, security
from app.database import get_db
from app.models import EmailSettings, User
from app.rate_limit import auth_limiter
from app.secret_reveal import reveal_secret
from app.services.mailer_shared import test_smtp_connection
from app.validation import MAX_PASSWORD_BYTES, normalize_email, normalize_optional_name, validate_password

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


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str

    @field_validator("current_password")
    @classmethod
    def validate_current(cls, value: str) -> str:
        if not value or len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("Enter your current password")
        return value

    @field_validator("new_password")
    @classmethod
    def validate_new(cls, value: str) -> str:
        return validate_password(value)


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


@router.put("/password")
def change_password(
    body: ChangePasswordBody,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(auth.get_current_user),
):
    """Change the signed-in user's own login password. The current password is
    required, so a left-open browser can't be used to take over the account."""
    auth_limiter.enforce(f"change-password:{user.id}", limit=8, window_seconds=15 * 60)
    if not auth.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Your current password is incorrect.")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="Choose a password different from your current one.")

    user.password_hash = auth.hash_password(body.new_password)
    db.commit()
    # The session cookie is tied to the old password; issue a fresh one so this
    # browser stays signed in (any other session of this account is signed out).
    auth.create_session(response, user)
    return {"ok": True}


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


@router.get("/email/password")
def reveal_email_app_password(
    db: Session = Depends(get_db),
    user: User = Depends(auth.get_current_user),
):
    """Return the signed-in user's own saved app password, for the eye icon on the
    Settings page. Only ever the caller's own row."""
    settings = db.query(EmailSettings).filter(
        EmailSettings.user_id == user.id,
        EmailSettings.is_deleted == False,  # noqa: E712
    ).first()
    revealed = reveal_secret(
        settings.app_password_encrypted if settings else None, user_id=user.id, scope="user-email",
    )
    return {"app_password": revealed["value"]}


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
