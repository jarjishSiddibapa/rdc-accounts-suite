"""Shared validation rules for account and email-facing API payloads."""

import re
import unicodedata

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_BYTES = 72

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 255 or not _EMAIL_RE.fullmatch(email):
        raise ValueError("Enter a valid email address")
    return email


def normalize_optional_name(value: str | None) -> str | None:
    """Normalize an optional human name without imposing ASCII-only rules."""
    if value is None:
        return None
    name = " ".join(value.split())
    if not name:
        return None
    if len(name) > 100:
        raise ValueError("Names must be 100 characters or fewer")
    if any(unicodedata.category(character).startswith("C") for character in name):
        raise ValueError("Name contains unsupported characters")
    return name


def validate_password(value: str) -> str:
    """Enforce the same "Strong" rule the frontend's password-strength meter
    requires before it will let a form submit (see
    frontend/src/lib/passwordStrength.ts) - length plus all four character
    classes - so the requirement can't be bypassed by calling the API
    directly. Keep both in sync if this changes."""
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password is too long")
    if value.isspace():
        raise ValueError("Password cannot contain only spaces")

    classes = sum(
        bool(re.search(pattern, value))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    if classes < 4:
        raise ValueError(
            "Password must be at least 10 characters and include uppercase, "
            "lowercase, a number, and a symbol"
        )
    return value
