"""The application's own system-level email sender - distinct from each
user's personal EmailSettings (used only for sending generated reports).
This one sends password-reset links and other system notifications, using
a single admin-configured identity (SystemEmailSettings, see models.py)."""

import logging
import smtplib
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app import security
from app.models import SystemEmailSettings

logger = logging.getLogger(__name__)


def seed_system_email(db: Session) -> None:
    """Create the singleton row without embedding any credentials."""
    if db.query(SystemEmailSettings).filter(SystemEmailSettings.id == 1).first() is not None:
        return
    row = SystemEmailSettings(
        id=1,
        sender_email=None,
        app_password_encrypted=None,
    )
    db.add(row)
    db.commit()


def get_system_email_settings(db: Session) -> dict:
    row = db.query(SystemEmailSettings).filter(
        SystemEmailSettings.id == 1,
        SystemEmailSettings.is_deleted == False,  # noqa: E712
    ).first()
    if row is None or not row.sender_email or not row.app_password_encrypted:
        return {"sender_email": None, "app_password": None, "configured": False}
    return {
        "sender_email": row.sender_email,
        "app_password": security.decrypt(row.app_password_encrypted),
        "configured": True,
    }


def test_smtp_connection(email: str, app_password: str) -> tuple[bool, str]:
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
            s.ehlo()
            s.starttls()
            s.login(email.strip(), app_password.strip())
        return True, "Connection and authentication successful."
    except smtplib.SMTPAuthenticationError:
        return False, (
            "Authentication failed. Check the Gmail address and app password. "
            "For Gmail: Google Account -> Security -> 2-Step Verification -> App Passwords."
        )
    except Exception:  # noqa: BLE001 - network failures are intentionally normalized
        logger.exception("System SMTP connection test failed")
        return False, "Connection failed. Check the network and SMTP settings, then try again."


def send_system_email(db: Session, to_email: str, subject: str, body_text: str) -> tuple[bool, str]:
    """Sends a plain-text system email (password reset, notifications).
    Returns (ok, message). Never raises - callers should treat a failed
    send as a soft error (log it, don't crash the request)."""
    settings = get_system_email_settings(db)
    if not settings["configured"]:
        return False, "System email is not configured yet - ask an admin to set it up."

    msg = MIMEText(body_text, "plain")
    msg["Subject"] = subject
    msg["From"] = settings["sender_email"]
    msg["To"] = to_email

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
            s.ehlo()
            s.starttls()
            s.login(settings["sender_email"], settings["app_password"])
            s.sendmail(settings["sender_email"], [to_email], msg.as_string())
        return True, "Sent."
    except Exception:  # noqa: BLE001
        logger.exception("System email delivery failed")
        return False, "Email delivery failed. Check the system email settings and network connection."


def send_password_reset_email(db: Session, to_email: str, reset_link: str) -> tuple[bool, str]:
    body = (
        "A password reset was requested for your RDC Accounts Suite account.\n\n"
        f"Reset your password using this link (valid for 1 hour):\n{reset_link}\n\n"
        "If you didn't request this, you can safely ignore this email."
    )
    return send_system_email(db, to_email, "Password Reset - RDC Accounts Suite", body)
