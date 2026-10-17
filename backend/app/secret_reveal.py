"""Shared "show saved password" support for the eye icon on settings forms."""

from fastapi import HTTPException

from app import security
from app.rate_limit import auth_limiter


def reveal_secret(token: str | None, *, user_id: int, scope: str) -> dict:
    """Decrypt a stored secret so the caller can see it. Callers must already have
    checked that this user is allowed to view it."""
    auth_limiter.enforce(f"reveal-secret:{scope}:{user_id}", limit=30, window_seconds=15 * 60)
    if not token:
        raise HTTPException(status_code=404, detail="Nothing is saved yet.")
    # security.decrypt returns "" when the token can't be decrypted (e.g. the key changed)
    value = security.decrypt(token)
    if not value:
        raise HTTPException(status_code=500, detail="The saved value could not be read. Enter it again and save.")
    return {"value": value}
