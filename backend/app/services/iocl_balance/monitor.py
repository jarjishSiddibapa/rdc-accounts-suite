"""Durable IOCL XTRAPOWER balance checks and notification delivery.

The browser automation mirrors the supplied ``hitanshi-iocl-balance-alerts``
reference: reuse a saved Playwright session, otherwise log in, navigate through
Financials -> Online CCMS Recharge, and prefer the exact CCMS Balance over the
rounded dashboard wallet amount. Credentials and browser storage state are
encrypted in MySQL and are never placed in background-job arguments/results.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from string import Formatter
from typing import Any

from sqlalchemy.orm import Session

from app import audit_middleware, security
from app.database import SessionLocal
from app.models import IoclBalanceCheck, IoclBalanceNotification, IoclBalanceSettings
from app.regional import format_indian_number, now_ist

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_URL = "https://beta.iocxtrapower.com/account/login?returnUrl=%2F"
DEFAULT_DAILY_SUBJECT = "IOCL Balance as on {date}"
DEFAULT_DAILY_BODY = """Dear Team,

IOCL Balance as on {date} is {balance}.

Thanks,
Ultrafine Team"""
DEFAULT_ALERT_SUBJECT = "Alert - IOCL CCMS balance is below {threshold}."
DEFAULT_ALERT_BODY = """Dear Team,

This is a reminder mail.

CCMS balance of IOCL has reached below {threshold}, Please recharge in priority.
Available CCMS balance – {balance}
"""

DAILY_TEMPLATE_FIELDS = frozenset({"date", "balance", "balance_number"})
ALERT_TEMPLATE_FIELDS = frozenset(
    {"date", "balance", "balance_number", "threshold", "threshold_number"}
)

_SUFFIX_MULTIPLIERS = {
    "K": Decimal("1000"),
    "L": Decimal("100000"),
    "CR": Decimal("10000000"),
}
_AMOUNT = r"([\d,]+(?:\.\d{1,2})?)\s*(K|L|Cr)?"
CHECK_MAX_ATTEMPTS = 3


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def seed_settings(db: Session) -> None:
    row = db.query(IoclBalanceSettings).filter(IoclBalanceSettings.id == 1).first()
    if row is not None:
        return
    db.add(
        IoclBalanceSettings(
            id=1,
            enabled=False,
            login_url=DEFAULT_LOGIN_URL,
            check_interval_minutes=30,
            login_timeout_seconds=60,
            daily_email_enabled=True,
            daily_email_time="08:00",
            daily_to="[]",
            daily_cc="[]",
            daily_subject_template=DEFAULT_DAILY_SUBJECT,
            daily_body_template=DEFAULT_DAILY_BODY,
            alerts_enabled=True,
            alert_start_amount=Decimal("500000"),
            alert_step_amount=Decimal("50000"),
            alert_repeat_hours=30,
            alert_to="[]",
            alert_cc="[]",
            alert_subject_template=DEFAULT_ALERT_SUBJECT,
            alert_body_template=DEFAULT_ALERT_BODY,
            version=1,
            is_deleted=False,
        )
    )
    db.commit()


def get_or_create_settings(db: Session) -> IoclBalanceSettings:
    row = db.query(IoclBalanceSettings).filter(IoclBalanceSettings.id == 1).first()
    if row is None:
        seed_settings(db)
        row = db.query(IoclBalanceSettings).filter(IoclBalanceSettings.id == 1).one()
    elif row.is_deleted:
        row.is_deleted = False
        db.commit()
    return row


def parse_recipients(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def validate_template(template: str, allowed_fields: frozenset[str]) -> None:
    try:
        fields = {
            field_name.split(".", 1)[0].split("[", 1)[0]
            for _, field_name, _, _ in Formatter().parse(template)
            if field_name
        }
    except ValueError as exc:
        raise ValueError("Template contains unmatched braces") from exc
    unsupported = fields - allowed_fields
    if unsupported:
        raise ValueError(f"Unsupported template placeholder: {sorted(unsupported)[0]}")


def parse_amount(number: str, suffix: str | None = None) -> Decimal:
    value = Decimal(number.replace(",", ""))
    if suffix:
        value *= _SUFFIX_MULTIPLIERS.get(suffix.strip().upper(), Decimal("1"))
    return value.quantize(Decimal("0.01"))


def extract_balance_from_text(body_text: str) -> Decimal | None:
    patterns = [
        rf"CCMS\s*Balance[^\d₹]{{0,100}}(?:₹|Rs\.?|INR)?\s*{_AMOUNT}",
        rf"Wallet\s*Balance[^\d₹]{{0,100}}(?:₹|Rs\.?|INR)?\s*{_AMOUNT}",
        rf"Available\s*Balance[^\d₹]{{0,100}}(?:₹|Rs\.?|INR)?\s*{_AMOUNT}",
        rf"Account\s*Balance[^\d₹]{{0,100}}(?:₹|Rs\.?|INR)?\s*{_AMOUNT}",
        rf"Current\s*Balance[^\d₹]{{0,100}}(?:₹|Rs\.?|INR)?\s*{_AMOUNT}",
    ]
    for pattern in patterns:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if not match:
            continue
        try:
            balance = parse_amount(match.group(1), match.group(2))
        except (ArithmeticError, ValueError):
            continue
        if Decimal("0") <= balance < Decimal("1000000000"):
            return balance

    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if "balance" not in line.lower():
            continue
        nearby = " ".join(lines[max(0, index - 2): min(len(lines), index + 4)])
        match = re.search(rf"(?:₹|Rs\.?|INR)\s*[:\-]?\s*{_AMOUNT}", nearby, re.IGNORECASE)
        if match:
            try:
                return parse_amount(match.group(1), match.group(2))
            except (ArithmeticError, ValueError):
                pass
    return None


def _ordinal_day(day: int) -> str:
    if 10 < day % 100 < 14:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_business_date(value=None) -> str:
    current = value or now_ist().date()
    return f"{_ordinal_day(current.day)} {current.strftime('%B %Y')}"


def format_amount(value: Decimal) -> str:
    return f"Rs. {format_indian_number(value, 2)}"


def format_threshold(value: Decimal) -> str:
    if value == 0:
        return "0"
    if value >= Decimal("10000000") and value % Decimal("100000") == 0:
        amount = value / Decimal("10000000")
        unit = "crore" if amount == 1 else "crore"
    elif value >= Decimal("100000"):
        amount = value / Decimal("100000")
        unit = "lakh" if amount == 1 else "lakh"
    elif value >= Decimal("1000"):
        amount = value / Decimal("1000")
        unit = "thousand"
    else:
        return format_indian_number(value, 0)
    rendered = format(amount.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered} {unit}"


def threshold_reminder_due(
    *,
    previous: Decimal | None,
    current: Decimal,
    threshold: Decimal,
    last_notification_at: datetime | None,
    repeat_hours: int,
    now: datetime,
) -> bool:
    """Return whether this check should create one below-threshold reminder.

    A newly entered below-threshold episode alerts immediately. While the
    balance remains below the threshold, reminders are spaced by the
    administrator-configured interval. A recovery to the threshold or above
    stops reminders and permits an immediate alert on a later drop.
    """
    if current >= threshold:
        return False
    if previous is None or previous >= threshold or last_notification_at is None:
        return True
    return last_notification_at <= now - timedelta(hours=max(1, repeat_hours))


def _dismiss_welcome_popup(page, timeout_ms: int = 800) -> None:
    try:
        button = page.get_by_text("Skip", exact=True).first
        button.wait_for(state="visible", timeout=timeout_ms)
        button.click()
        page.wait_for_timeout(300)
    except Exception:
        pass


def _find_login_fields(page):
    page.wait_for_timeout(1000)
    for frame in page.frames:
        try:
            inputs = frame.locator("input")
            password = None
            username = None
            for index in range(inputs.count()):
                field = inputs.nth(index)
                if (field.get_attribute("type") or "").lower() == "password":
                    password = field
                combined = " ".join(
                    (field.get_attribute(name) or "").lower()
                    for name in ("name", "id", "placeholder")
                )
                if field.is_visible() and any(word in combined for word in ("user", "login", "customer")):
                    username = username or field
            if username is None:
                for index in range(inputs.count()):
                    field = inputs.nth(index)
                    if field.is_visible() and (field.get_attribute("type") or "text").lower() in {"text", "email", ""}:
                        username = field
                        break
            if username is not None and password is not None:
                return frame, username, password
        except Exception:
            continue
    return None, None, None


def _click_login(frame) -> bool:
    for selector in ('button[type="submit"]', 'input[type="submit"]'):
        try:
            target = frame.locator(selector).first
            if target.is_visible():
                target.click()
                return True
        except Exception:
            pass
    try:
        candidates = frame.locator("button, a, input[type=button]")
        for index in range(candidates.count()):
            target = candidates.nth(index)
            if target.is_visible() and any(
                word in (target.inner_text() or "").strip().lower()
                for word in ("log in", "login", "sign in")
            ):
                target.click()
                return True
    except Exception:
        pass
    return False


def _wait_until_logged_in(page, timeout_seconds: int) -> bool:
    """Confirm that the portal has stayed away from the login route.

    An expired XTRAPOWER session can briefly visit the SPA landing route while
    its authentication guard is starting, then bounce back to ``/account/login``.
    Treating the first away-from-login URL as success leaves the checker on the
    login page, whose hidden Angular templates still contain labels such as
    ``Financials``. Require three consecutive observations (about 1.5 seconds)
    on an authenticated route before accepting the session or fresh login.
    """
    poll_ms = 500
    required_stable_polls = 3
    stable_polls = 0
    max_polls = max(1, int(timeout_seconds * 1000 / poll_ms))
    for _ in range(max_polls):
        try:
            away_from_login = "/account/login" not in page.url.lower()
        except Exception:
            away_from_login = False
        stable_polls = stable_polls + 1 if away_from_login else 0
        if stable_polls >= required_stable_polls:
            return True
        page.wait_for_timeout(poll_ms)
    return False


def _wait_for_overlays_gone(page, timeout_ms: int = 4000) -> None:
    """Best-effort wait for the portal's intermittent blocking UI layers."""
    _dismiss_welcome_popup(page, timeout_ms=min(timeout_ms, 800))
    try:
        page.locator(".ngx-spinner-overlay").first.wait_for(
            state="hidden",
            timeout=timeout_ms,
        )
    except Exception:
        pass


def _find_visible_nav_link(page, label: str, timeout_ms: int):
    """Return the first genuinely visible element matching `label`.

    The portal's nav renders more than one element containing this text at
    once (e.g. a collapsed/duplicate menu the SPA keeps in the DOM but
    hidden) - plain get_by_text(...).first grabs whichever one comes first
    in DOM order regardless of visibility, so it can latch onto a match
    that is permanently hidden and wait_for(state="visible") on it times
    out even though a visible one exists elsewhere on the page. Poll all
    matches each pass and pick the first one that is actually visible,
    matching the same pattern already used in _find_login_fields/_click_login.
    """
    candidates = page.get_by_text(label, exact=False)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        try:
            count = candidates.count()
            for index in range(count):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(200)


def _click_nav(page, label: str, timeout_ms: int = 8000) -> None:
    _wait_for_overlays_gone(page, timeout_ms=min(timeout_ms, 3000))
    link = _find_visible_nav_link(page, label, timeout_ms)
    if link is None:
        raise RuntimeError(f"No visible '{label}' navigation link was found")
    last_error = None
    for _ in range(3):
        try:
            _wait_for_overlays_gone(page, timeout_ms=2000)
            link.click(timeout=4000)
            return
        except Exception as exc:  # overlay may have appeared between wait and click
            last_error = exc
            replacement = _find_visible_nav_link(page, label, 2000)
            if replacement is not None:
                link = replacement
    raise RuntimeError(f"Could not open {label}") from last_error


def fetch_balance(
    *,
    login_url: str,
    username: str,
    password: str,
    saved_session: dict[str, Any] | None,
    login_timeout_seconds: int,
) -> tuple[Decimal, dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed on the server") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                storage_state=saved_session,
            )
            page = context.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
            _dismiss_welcome_popup(page)

            logged_in = bool(saved_session) and _wait_until_logged_in(page, 8)
            if not logged_in:
                frame, username_field, password_field = _find_login_fields(page)
                if username_field is None or password_field is None:
                    raise RuntimeError("The IOCL login form could not be detected")
                username_field.fill(username)
                password_field.fill(password)
                if not _click_login(frame):
                    raise RuntimeError("The IOCL login button could not be detected")
                if not _wait_until_logged_in(page, login_timeout_seconds):
                    raise RuntimeError(
                        "IOCL login timed out. The credentials may be invalid or CAPTCHA may require a refreshed saved session."
                    )

            # The portal is a client-side SPA: its URL changes to Quicklinks
            # before the navigation drawer is rendered. The reference waits
            # for that post-login render too; without it, a valid fresh login
            # can race the Financials click.
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(1000)
            _dismiss_welcome_popup(page)
            _click_nav(page, "Financials")
            page.wait_for_timeout(1000)
            _click_nav(page, "Online CCMS Recharge", timeout_ms=5000)

            balance = None
            for _ in range(10):
                _dismiss_welcome_popup(page, timeout_ms=300)
                for current_page in reversed(context.pages):
                    try:
                        text = current_page.locator("body").inner_text(timeout=5000)
                    except Exception:
                        continue
                    balance = extract_balance_from_text(text)
                    if balance is not None:
                        break
                if balance is not None:
                    break
                page.wait_for_timeout(1500)
            if balance is None:
                raise RuntimeError("The exact IOCL CCMS balance could not be detected")
            return balance, context.storage_state()
        finally:
            browser.close()


def fetch_balance_with_retries(snapshot: dict[str, Any], progress_cb=None):
    """Try a portal balance check at most three times.

    The first attempt may reuse the encrypted Playwright storage state. Later
    attempts deliberately start with a fresh browser login so an expired or
    partially corrupted portal session cannot poison every retry.
    """
    last_error: Exception | None = None
    for attempt in range(1, CHECK_MAX_ATTEMPTS + 1):
        if progress_cb:
            progress_cb(
                0.05 + (attempt - 1) * 0.12,
                f"Opening the secure IOCL portal (attempt {attempt} of {CHECK_MAX_ATTEMPTS})",
            )
        attempt_snapshot = dict(snapshot)
        if attempt > 1:
            attempt_snapshot["saved_session"] = None
        try:
            balance, session_state = fetch_balance(**attempt_snapshot)
            return balance, session_state, attempt
        except Exception as exc:  # noqa: BLE001 - each portal failure is retryable
            last_error = exc
            logger.warning(
                "IOCL portal attempt %s of %s failed: %s",
                attempt,
                CHECK_MAX_ATTEMPTS,
                str(exc)[:500],
            )
    message = str(last_error) if last_error is not None else "Unknown portal error"
    raise RuntimeError(
        f"IOCL balance check failed after {CHECK_MAX_ATTEMPTS} attempts. Last error: {message}"
    ) from last_error


def _render(template: str, balance: Decimal, threshold: Decimal | None = None) -> str:
    values = {
        "date": format_business_date(),
        "balance": format_amount(balance),
        "balance_number": format_indian_number(balance, 2),
        "threshold": format_threshold(threshold or Decimal("0")),
        "threshold_number": format_indian_number(threshold or Decimal("0"), 2),
    }
    return template.format_map(values)


def _render_html_body(
    template: str,
    balance: Decimal,
    threshold: Decimal | None = None,
) -> str:
    """Render the configured body with every balance placeholder bold."""
    values = {
        "date": format_business_date(),
        "balance": f"<strong>{format_amount(balance)}</strong>",
        "balance_number": f"<strong>{format_indian_number(balance, 2)}</strong>",
        "threshold": format_threshold(threshold or Decimal("0")),
        "threshold_number": format_indian_number(threshold or Decimal("0"), 2),
    }
    return template.format_map(values)


def render_preview(
    subject_template: str,
    body_template: str,
    balance: Decimal,
    threshold: Decimal | None = None,
) -> tuple[str, str]:
    """Render a subject/body template pair with sample data, for the
    Settings page's "Send test mail" buttons - lets someone see the real
    rendered wording before saving, without waiting for an actual balance
    check or threshold crossing."""
    return _render(subject_template, balance, threshold), _render_html_body(
        body_template, balance, threshold
    )


def _add_or_refresh_daily_notification(
    db: Session,
    settings: IoclBalanceSettings,
    check: IoclBalanceCheck,
    balance: Decimal,
) -> None:
    current = now_ist()
    if not settings.daily_email_enabled or current.strftime("%H:%M") < settings.daily_email_time:
        return
    if settings.last_daily_sent_date == current.date():
        return
    key = f"daily:{current.date().isoformat()}"
    row = db.query(IoclBalanceNotification).filter(
        IoclBalanceNotification.notification_key == key,
    ).first()
    values = dict(
        check_id=check.id,
        balance=balance,
        subject=_render(settings.daily_subject_template, balance),
        body=_render_html_body(settings.daily_body_template, balance),
        to_recipients=settings.daily_to or "[]",
        cc_recipients=settings.daily_cc or "[]",
    )
    if row is None:
        row = IoclBalanceNotification(
            notification_key=key,
            notification_type="daily",
            status="pending",
            **values,
        )
        db.add(row)
    elif row.status == "failed":
        for field, value in values.items():
            setattr(row, field, value)
        row.status = "pending"
        row.error_message = None


def _add_threshold_notifications(
    db: Session,
    settings: IoclBalanceSettings,
    check: IoclBalanceCheck,
    previous: Decimal | None,
    balance: Decimal,
) -> None:
    if not settings.alerts_enabled:
        return
    threshold = Decimal(settings.alert_start_amount)
    last_notification = db.query(IoclBalanceNotification).filter(
        IoclBalanceNotification.notification_type == "threshold",
        IoclBalanceNotification.is_deleted.is_(False),
    ).order_by(IoclBalanceNotification.created_at.desc()).first()
    now = _utcnow()
    if not threshold_reminder_due(
        previous=previous,
        current=balance,
        threshold=threshold,
        last_notification_at=(last_notification.created_at if last_notification else None),
        repeat_hours=settings.alert_repeat_hours,
        now=now,
    ):
        return
    db.add(
        IoclBalanceNotification(
            notification_key=f"threshold-reminder:{check.id}",
            check_id=check.id,
            notification_type="threshold",
            threshold_amount=threshold,
            balance=balance,
            subject=_render(settings.alert_subject_template, balance, threshold),
            body=_render_html_body(settings.alert_body_template, balance, threshold),
            to_recipients=settings.alert_to or "[]",
            cc_recipients=settings.alert_cc or "[]",
            status="pending",
            created_at=now,
        )
    )


def _send_from_configured_sender(
    sender_email: str | None,
    sender_app_password_encrypted: str | None,
    to_emails: list[str],
    cc_emails: list[str],
    subject: str,
    body: str,
) -> tuple[bool, str]:
    """Send only from the dedicated admin-owned IOCL sender.

    The encrypted password is loaded from the singleton settings row at send
    time, never placed in the durable job payload or notification history.
    """
    if not sender_email or not sender_app_password_encrypted:
        return False, "The IOCL sender email and app password are not configured."

    from app.services.mailer_shared import send_mail

    try:
        send_mail(
            from_email=sender_email,
            app_password=security.decrypt(sender_app_password_encrypted),
            to_addresses=to_emails,
            cc_addresses=cc_emails,
            subject=subject,
            html_body=body.replace("\n", "<br>"),
            attachments=[],
        )
        return True, f"Sent from {sender_email}"
    except Exception as exc:  # noqa: BLE001 - report as a soft send failure
        return False, str(exc)


def _deliver_notification(notification_id: int) -> dict:
    db = SessionLocal()
    try:
        row = db.query(IoclBalanceNotification).filter(
            IoclBalanceNotification.id == notification_id,
            IoclBalanceNotification.is_deleted.is_(False),
        ).with_for_update().first()
        if row is None or row.status not in {"pending", "failed"}:
            return {"id": notification_id, "status": "skipped"}
        to_emails = parse_recipients(row.to_recipients)
        cc_emails = parse_recipients(row.cc_recipients)
        row.status = "sending"
        row.attempted_at = _utcnow()
        row.error_message = None
        db.commit()

        settings = get_or_create_settings(db)
        ok, message = _send_from_configured_sender(
            settings.sender_email,
            settings.sender_app_password_encrypted,
            to_emails,
            cc_emails,
            row.subject,
            row.body,
        )
        row = db.query(IoclBalanceNotification).filter_by(id=notification_id).one()
        if ok:
            row.status = "sent"
            row.sent_at = _utcnow()
            if row.notification_type == "daily":
                settings = get_or_create_settings(db)
                settings.last_daily_sent_date = now_ist().date()
        else:
            row.status = "failed"
            row.error_message = message
        db.commit()
        audit_middleware.log_event(
            f"iocl.notification.{row.status}",
            details={"type": row.notification_type, "threshold": str(row.threshold_amount) if row.threshold_amount is not None else None},
        )
        return {"id": notification_id, "status": row.status, "message": message}
    finally:
        db.close()


def _release_check_lock(token: str) -> None:
    db = SessionLocal()
    try:
        settings = get_or_create_settings(db)
        if settings.check_lock_token == token:
            settings.check_lock_token = None
            settings.check_lock_expires_at = None
            db.commit()
    finally:
        db.close()


def run_check_job(trigger: str = "scheduled", progress_cb=None) -> dict:
    """Background task. It reloads secrets from MySQL; args contain no secret."""
    started = time.monotonic()
    token = str(uuid.uuid4())
    db = SessionLocal()
    try:
        settings = db.query(IoclBalanceSettings).filter(
            IoclBalanceSettings.id == 1,
        ).with_for_update().first()
        if settings is None:
            seed_settings(db)
            settings = db.query(IoclBalanceSettings).filter_by(id=1).with_for_update().one()
        now = _utcnow()
        if settings.check_lock_token and settings.check_lock_expires_at and settings.check_lock_expires_at > now:
            db.add(
                IoclBalanceCheck(
                    trigger=trigger,
                    status="skipped",
                    error_message="Another IOCL balance check was already running.",
                    checked_at=now,
                    duration_seconds=round(time.monotonic() - started, 3),
                )
            )
            db.commit()
            return {"skipped": True, "message": "Another IOCL balance check is already running."}
        username = (settings.username or "").strip()
        password = security.decrypt(settings.password_encrypted or "")
        if not username or not password:
            # Raised before the try/except below that repackages failures as
            # JobUserError, so it must be one itself - otherwise the user
            # sees jobs.py's generic "internal error" message instead of
            # this actionable one for what is really just a missing-config
            # validation case, not a real failure.
            from app.jobs import JobUserError
            raise JobUserError("Configure the IOCL username and password before checking the balance")
        try:
            saved_session = json.loads(security.decrypt(settings.session_state_encrypted or "")) if settings.session_state_encrypted else None
        except (TypeError, ValueError):
            saved_session = None
        snapshot = {
            "login_url": settings.login_url,
            "username": username,
            "password": password,
            "saved_session": saved_session,
            "login_timeout_seconds": settings.login_timeout_seconds,
        }
        settings.check_lock_token = token
        # Three complete portal attempts can legitimately outlive the old
        # fixed 15-minute lease when the administrator chooses the maximum
        # login timeout. Keep the database lease longer than the bounded
        # worst case so another worker cannot overlap the same portal account.
        lease_seconds = max(
            15 * 60,
            CHECK_MAX_ATTEMPTS * (settings.login_timeout_seconds + 120),
        )
        settings.check_lock_expires_at = now + timedelta(seconds=lease_seconds)
        db.commit()
    finally:
        db.close()

    try:
        balance, session_state, attempt_count = fetch_balance_with_retries(
            snapshot, progress_cb=progress_cb
        )
        if progress_cb:
            progress_cb(0.72, "CCMS balance detected; evaluating notifications")

        db = SessionLocal()
        try:
            settings = db.query(IoclBalanceSettings).filter_by(id=1).with_for_update().one()
            previous = Decimal(settings.last_balance) if settings.last_balance is not None else None
            check = IoclBalanceCheck(
                trigger=trigger,
                status="success",
                balance=balance,
                checked_at=_utcnow(),
                duration_seconds=round(time.monotonic() - started, 3),
            )
            db.add(check)
            db.flush()
            settings.session_state_encrypted = security.encrypt(json.dumps(session_state, separators=(",", ":")))
            settings.last_balance = balance
            settings.last_checked_at = check.checked_at
            settings.last_check_status = "success"
            settings.last_error = None
            settings.updated_at = _utcnow()
            if trigger == "scheduled":
                # A manual "Check balance now" click is someone looking up
                # today's number on demand - it must never fire the once-a-day
                # morning mail (that already surprised a user in production).
                # Threshold alerts still fire on a manual check too, since a
                # real crossing is worth knowing about immediately.
                _add_or_refresh_daily_notification(db, settings, check, balance)
            _add_threshold_notifications(db, settings, check, previous, balance)
            db.commit()
            check_id = check.id
        finally:
            db.close()

        db = SessionLocal()
        try:
            notification_ids = [
                row.id for row in db.query(IoclBalanceNotification).filter(
                    IoclBalanceNotification.status.in_(["pending", "failed"]),
                    IoclBalanceNotification.is_deleted.is_(False),
                ).order_by(IoclBalanceNotification.created_at.asc()).limit(50).all()
            ]
        finally:
            db.close()
        deliveries = [_deliver_notification(notification_id) for notification_id in notification_ids]
        if progress_cb:
            progress_cb(1.0, "IOCL balance check complete")
        audit_middleware.log_event(
            "iocl.balance.checked",
            details={
                "trigger": trigger,
                "check_id": check_id,
                "attempts": attempt_count,
                "notifications": len(deliveries),
            },
        )
        return {
            "check_id": check_id,
            "balance": float(balance),
            "checked_at": _utcnow().isoformat(),
            "attempts": attempt_count,
            "notifications": deliveries,
        }
    except Exception as exc:
        logger.exception("IOCL balance check failed")
        db = SessionLocal()
        try:
            settings = get_or_create_settings(db)
            check = IoclBalanceCheck(
                trigger=trigger,
                status="error",
                error_message=str(exc)[:2000],
                checked_at=_utcnow(),
                duration_seconds=round(time.monotonic() - started, 3),
            )
            db.add(check)
            settings.last_checked_at = check.checked_at
            settings.last_check_status = "error"
            settings.last_error = str(exc)[:2000]
            settings.updated_at = _utcnow()
            db.commit()
        finally:
            db.close()
        audit_middleware.log_event("iocl.balance.failed", details={"trigger": trigger, "error": type(exc).__name__})
        from app.jobs import JobUserError
        raise JobUserError(str(exc)) from exc
    finally:
        _release_check_lock(token)


def enqueue_due_check() -> str | None:
    """Called by the one supervised scheduler. Durable time state is in MySQL."""
    db = SessionLocal()
    try:
        settings = db.query(IoclBalanceSettings).filter(
            IoclBalanceSettings.id == 1,
            IoclBalanceSettings.is_deleted.is_(False),
        ).with_for_update().first()
        if settings is None or not settings.enabled or not settings.username or not settings.password_encrypted:
            return None
        now = _utcnow()
        current = now_ist()
        daily_due = (
            settings.daily_email_enabled
            and current.strftime("%H:%M") >= settings.daily_email_time
            and settings.last_daily_sent_date != current.date()
            and (
                settings.last_daily_attempt_at is None
                or settings.last_daily_attempt_at
                <= now - timedelta(minutes=settings.check_interval_minutes)
            )
        )
        interval_due = settings.next_check_at is None or settings.next_check_at <= now
        if not daily_due and not interval_due:
            return None
        settings.next_check_at = now + timedelta(minutes=settings.check_interval_minutes)
        if daily_due:
            settings.last_daily_attempt_at = now
        db.commit()
    finally:
        db.close()

    from app.jobs import submit_job
    return submit_job(run_check_job, "scheduled", owner_id=0, cancel_on_disconnect=False)
