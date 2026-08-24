"""Fernet symmetric encryption helpers for the stored email app-password.

The key is generated once and persisted at config.SECRET_KEY_PATH. It is
also reused (read as raw bytes) by app.auth as the itsdangerous session
signing secret, so the whole suite has a single stable, non-committed
secret file.
"""

import os

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock

from app import config


def _load_or_create_key() -> bytes:
    lock = FileLock(str(config.SECRET_KEY_PATH) + ".lock", timeout=10)
    with lock:
        if config.SECRET_KEY_PATH.exists():
            key = config.SECRET_KEY_PATH.read_bytes()
            try:
                Fernet(key)
            except (ValueError, TypeError) as exc:
                raise RuntimeError("The application secret key is invalid") from exc
            return key

        key = Fernet.generate_key()
        temporary_path = config.SECRET_KEY_PATH.with_suffix(".tmp")
        temporary_path.write_bytes(key)
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            pass
        temporary_path.replace(config.SECRET_KEY_PATH)
        return key


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    f = Fernet(_load_or_create_key())
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        f = Fernet(_load_or_create_key())
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""
