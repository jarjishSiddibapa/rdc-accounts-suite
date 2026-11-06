"""Send history for the applications that mail by hand (Unaccounted Transactions, Balance Confirmation, Payment
Reminder, FSE Reminder): every send is recorded in the same log the automatic mails use, so each application
shows who sent what, when and with what outcome.

Recording is best effort: a problem here is logged and never turns a mail that went out into an error."""

import logging
from datetime import UTC, datetime

from app.database import SessionLocal
from app.models import User
from app.services import scheduled_report_mail as mail

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def login_email(user_id: int) -> str | None:
    db = SessionLocal()
    try:
        row = db.query(User.email).filter(User.id == user_id).first()
        return row[0] if row else None
    finally:
        db.close()


def view(db, app_key: str, limit: int = 10, offset: int = 0) -> dict:
    """The application's send history, newest first (same shape as the automatic mails' log)."""
    return mail.log_view(db, app_key, limit=limit, offset=offset)


def record(app_key: str, user_id: int, started_at: datetime, status: str, error: str | None = None, *,
           to: list[str] | None = None, cc: list[str] | None = None, messages: list[dict] | None = None,
           detail: dict | None = None) -> None:
    try:
        mail.record_manual_send(
            app_key, login_email(user_id), started_at, status, error,
            {"to": list(to or []), "cc": list(cc or [])}, list(messages or []), dict(detail or {}),
        )
    except Exception:  # noqa: BLE001 - see the module docstring
        logger.exception("Could not record the %s send in its history", app_key)


def record_bulk(app_key: str, user_id: int, started_at: datetime, report: list[dict],
                detail: dict | None = None) -> None:
    """One history entry for a batch of per-customer mails: how many went, failed or were skipped."""
    sent = sum(1 for item in report if item.get("status") == "sent")
    failed = sum(1 for item in report if item.get("status") == "failed")
    skipped = sum(1 for item in report if item.get("status") == "skipped")
    status = "sent" if sent and not failed else ("failed" if not sent else "partial")
    first_problem = next((item.get("detail") for item in report if item.get("status") == "failed"), None)
    record(
        app_key, user_id, started_at, status,
        (f"{failed} of {sent + failed + skipped} mails failed. First problem: {first_problem}"[:1900] if failed else None),
        detail={**(detail or {}), "sent": sent, "failed": failed, "skipped": skipped},
    )
