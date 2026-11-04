"""Login / logout / current-user routes."""

import functools
import logging
import socket
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app import auth, config, system_mailer
from app.database import get_db
from app.models import User
from app.permissions import parse_allowed_apps
from app.rate_limit import auth_limiter
from app.validation import MAX_PASSWORD_BYTES, normalize_email, validate_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _own_host_names() -> frozenset[str]:
    """The names and addresses this server answers to."""
    names = {"localhost", "127.0.0.1", "::1"}
    try:
        host = socket.gethostname()
        names.update({host.lower(), socket.getfqdn().lower()})
        names.update(info[4][0].lower() for info in socket.getaddrinfo(host, None))
    except OSError:
        pass
    return frozenset(names)


def _reset_link_base(request: Request) -> str | None:
    """Where a password-reset link points. APP_BASE_URL when it is set. Otherwise the address the request
    came to, but only if it is one of this server's own: the Host header is sent by the client, and a link
    built from a forged one would carry the reset token to a server of the sender's choosing."""
    if config.APP_BASE_URL:
        return config.APP_BASE_URL
    try:
        host = urlsplit("//" + request.headers.get("host", "")).hostname
    except ValueError:
        return None
    if host is None or host.lower() not in _own_host_names():
        return None
    return str(request.base_url).rstrip("/")


class LoginBody(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def validate_login_password(cls, value: str) -> str:
        if not value or len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("Invalid password")
        return value


class ForgotPasswordBody(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 2_048:
            raise ValueError("Invalid reset token")
        return value

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)


def _me_dict(user: User) -> dict:
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": user.role,
        # Admins are inherently unrestricted. Regular users always receive
        # their explicit grants; legacy NULL values fail closed to [].
        "allowed_apps": None if user.role == "admin" else parse_allowed_apps(user),
    }


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    client = request.client.host if request.client else "unknown"
    auth_limiter.enforce(f"login-ip:{client}", limit=20, window_seconds=5 * 60)
    auth_limiter.enforce(f"login-account:{body.email}", limit=8, window_seconds=5 * 60)
    user = (
        db.query(User)
        .filter(User.email == body.email, User.is_deleted == False)  # noqa: E712
        .first()
    )
    if user is None:
        auth.spend_password_check_time(body.password)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="This account has been deactivated. Contact your admin.",
        )

    auth.create_session(response, user)
    return _me_dict(user)


@router.post("/logout")
def logout(response: Response):
    auth.clear_session(response)
    return {"ok": True}


@router.post("/activity")
def activity(response: Response, user: User = Depends(auth.get_current_user)):
    """Refresh the signed idle timestamp after genuine browser activity."""
    auth.create_session(response, user)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(auth.get_current_user)):
    return _me_dict(user)


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordBody, request: Request, db: Session = Depends(get_db)):
    """Always returns the same generic message regardless of whether the
    email exists, to avoid leaking which addresses have accounts."""
    client = request.client.host if request.client else "unknown"
    auth_limiter.enforce(f"forgot-ip:{client}", limit=10, window_seconds=15 * 60)
    auth_limiter.enforce(f"forgot-account:{body.email}", limit=4, window_seconds=15 * 60)

    user = (
        db.query(User)
        .filter(User.email == body.email, User.is_deleted == False)  # noqa: E712
        .first()
    )
    if user is not None and user.is_active:
        base_url = _reset_link_base(request)
        if base_url is None:
            logger.warning(
                "Password reset email not sent: the request's Host header %r is not this server's own name or "
                "address. Set APP_BASE_URL in .env to the address users open.", request.headers.get("host"),
            )
        else:
            token = auth.create_reset_token(user)
            system_mailer.send_password_reset_email(db, user.email, f"{base_url}/reset-password?token={token}")
    return {"message": "If that email has an account, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(body: ResetPasswordBody, request: Request, db: Session = Depends(get_db)):
    client = request.client.host if request.client else "unknown"
    auth_limiter.enforce(f"reset-ip:{client}", limit=12, window_seconds=15 * 60)
    payload = auth.verify_reset_token(body.token)
    if payload is None or not isinstance(payload.get("user_id"), int):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    user = db.query(User).filter(User.id == payload["user_id"], User.is_deleted == False).first()  # noqa: E712
    if user is None or not user.is_active or not auth.reset_token_matches_user(payload, user):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    user.password_hash = auth.hash_password(body.new_password)
    db.commit()
    return {"ok": True}
