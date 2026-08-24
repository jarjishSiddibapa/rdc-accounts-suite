"""Password hashing, session cookie signing, and auth dependencies."""

import hashlib
import hmac
import time

import bcrypt
import itsdangerous
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app import config, security
from app.database import get_db
from app.models import User
from app.validation import validate_password

# Passwords are hashed with bcrypt directly (not via passlib's CryptContext):
# passlib 1.7.4's bcrypt backend self-test is incompatible with bcrypt>=4.1
# and raises ValueError during startup - using the bcrypt package directly
# avoids that whole class of bug and has no extra dependency to go stale.
_BCRYPT_MAX_BYTES = 72  # bcrypt's own input limit


def hash_password(password: str) -> str:
    validate_password(password)
    pw_bytes = password.encode("utf-8")
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        pw_bytes = password.encode("utf-8")
        if len(pw_bytes) > _BCRYPT_MAX_BYTES:
            return False
        return bcrypt.checkpw(pw_bytes, hashed.encode("utf-8"))
    except ValueError:
        return False


# Reuse the same persisted key file that security.py uses for Fernet
# encryption as a stable, non-committed secret for signing session cookies
# (and, with a different salt, password-reset tokens).
_SECRET_KEY_BYTES = security._load_or_create_key()
_SIGNING_KEY = hmac.new(_SECRET_KEY_BYTES, b"session-signing", hashlib.sha256).digest()
serializer = itsdangerous.URLSafeTimedSerializer(_SIGNING_KEY)
_reset_serializer = itsdangerous.URLSafeTimedSerializer(
    _SIGNING_KEY, salt="password-reset"
)

SESSION_MAX_AGE = 60 * 60 * 24 * 7  # Absolute signed-token validity.
SESSION_IDLE_TIMEOUT = 30 * 60
RESET_TOKEN_MAX_AGE = 60 * 60  # 1 hour


def create_session(response: Response, user: User) -> None:
    token = serializer.dumps(
        {
            "user_id": user.id,
            "email": user.email,
            "credential": _credential_marker(user),
            # Signed into the token so the idle limit remains enforceable after
            # a server restart and cannot be extended by modifying a browser value.
            "last_activity": int(time.time()),
        }
    )
    response.set_cookie(
        "session",
        token,
        httponly=True,
        samesite="lax",
        secure=config.SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(
        "session",
        path="/",
        secure=config.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )


def _credential_marker(user: User) -> str:
    material = f"{user.password_hash}\0{user.email}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def _load_session_data(request: Request) -> dict | None:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (itsdangerous.BadSignature, itsdangerous.SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None

    last_activity = data.get("last_activity")
    if not isinstance(last_activity, int):
        # Tokens issued before idle-session enforcement require a fresh login.
        return None
    if time.time() - last_activity >= SESSION_IDLE_TIMEOUT:
        return None
    return data


def get_session_identity(request: Request) -> tuple[int | None, str | None]:
    data = _load_session_data(request)
    if data is None:
        return None, None
    user_id = data.get("user_id")
    email = data.get("email")
    return (user_id if isinstance(user_id, int) else None, email if isinstance(email, str) else None)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    data = _load_session_data(request)
    if data is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = db.query(User).filter(User.id == data.get("user_id")).first()
    if (
        user is None
        or user.is_deleted
        or not user.is_active
        or not hmac.compare_digest(str(data.get("credential", "")), _credential_marker(user))
    ):
        # Re-checked on every request (not just at login) so a session
        # already in progress is cut off the moment an admin deactivates or
        # removes the account, not just on the next login attempt.
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def create_reset_token(user: User) -> str:
    return _reset_serializer.dumps(
        {"user_id": user.id, "credential": _credential_marker(user)}
    )


def verify_reset_token(token: str) -> dict | None:
    """Return the signed reset payload, or ``None`` if invalid/expired."""
    try:
        data = _reset_serializer.loads(token, max_age=RESET_TOKEN_MAX_AGE)
    except (itsdangerous.BadSignature, itsdangerous.SignatureExpired):
        return None
    return data if isinstance(data, dict) else None


def reset_token_matches_user(data: dict, user: User) -> bool:
    return data.get("user_id") == user.id and hmac.compare_digest(
        str(data.get("credential", "")), _credential_marker(user)
    )


def seed_initial_users(db: Session) -> None:
    if db.query(User).count() == 0:
        if not config.INITIAL_ADMIN_EMAIL or not config.INITIAL_ADMIN_PASSWORD:
            raise RuntimeError(
                "The database has no users. Set INITIAL_ADMIN_EMAIL and "
                "INITIAL_ADMIN_PASSWORD in backend/.env for the first startup."
            )
        admin = User(
            email=config.INITIAL_ADMIN_EMAIL,
            password_hash=hash_password(config.INITIAL_ADMIN_PASSWORD),
            role="admin",
        )
        db.add(admin)
        db.commit()
