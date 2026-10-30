"""Automatic report mail, shared by the Untagged Invoices Report, the GST
Invoice Number Adder and the Debtors Reports Auto Mailer.

An administrator chooses, per application, whether the report is mailed
automatically, on which weekdays and at what time (IST), the wording, and
whether to hold the mail when mappings are missing. The To / Cc recipients are
the application's admin-managed defaults (the same list shown under Admin ->
Report recipients). "Send now" runs the very same job on demand.

The mail goes out through the system email identity (Admin -> Email
settings) unless the administrator picks a person: a scheduled run has no
signed-in user, so it is sent from that person's own email (Settings) instead.

The scheduler process calls ``enqueue_due_checks`` every ~45 seconds; the
report itself is built and mailed by a durable background job
(``run_mail_job``), so a restart or a closed browser never loses a run.
"""

from __future__ import annotations

import calendar
import html
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dtime, timedelta
from pathlib import Path
from string import Formatter

from sqlalchemy.orm import Session

from app import audit_middleware, security
from app.database import SessionLocal
from app.jobs import JobCancelled, JobUserError
from app.models import EmailSettings, ScheduledReportMail, ScheduledReportMailLog, User
from app.regional import now_ist
from app.services.mailer_shared import (
    _plain_to_html,
    get_report_recipient_defaults,
    send_mail,
    set_report_recipient_defaults,
)
from app.system_mailer import get_system_email_settings
from app.validation import normalize_email

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULT_SEND_TIME = "08:30"
DEFAULT_SEND_DAYS = (0, 1, 2, 3, 4, 5)  # Monday to Saturday
_TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
# A run that has not finished after this long is treated as dead (a crashed
# worker), so it cannot block the next run forever.
_RUN_STALE_AFTER = timedelta(minutes=90)

# Gmail refuses a message over 25 MB, and base64 makes an attachment about a
# third larger, so a single attachment has to stay comfortably below ~18 MB.
MAX_ATTACHMENT_BYTES = 17 * 1024 * 1024


@dataclass(frozen=True)
class MailSpec:
    app_key: str
    label: str
    default_subject: str
    default_intro: str
    placeholders: tuple[str, ...]
    scheduled: bool = True      # False: only ever mailed by hand (preview, then confirm)


SPECS: dict[str, MailSpec] = {
    "untagged-invoices-report": MailSpec(
        app_key="untagged-invoices-report",
        label="Untagged Invoices Report",
        default_subject="Untagged Invoices Report as on {date}",
        default_intro=(
            "Dear Team,\n\n"
            "Please find below the Untagged Summary and the Below 1k Summary as on {date}. "
            "The detailed report is attached."
        ),
        placeholders=("date",),
    ),
    "gst-invoice-adder": MailSpec(
        app_key="gst-invoice-adder",
        label="GST Invoice Number Report",
        default_subject="GST Invoice Number Report as on {date}",
        default_intro=(
            "Dear Team,\n\n"
            "Please find attached the receivable ageing as on {date} with the GST invoice numbers added."
        ),
        placeholders=("date", "total", "found", "blank"),
    ),
    "debtors-reports-auto-mailer": MailSpec(
        app_key="debtors-reports-auto-mailer",
        label="Debtors Reports",
        default_subject="Debtors Reports as on {date}",
        default_intro=(
            "Dear Team,\n\n"
            "Please find below:\n\n"
            "1. Ageing with GST invoice numbers as on {date}\n"
            "2. Unapplied Receipts as on {date}\n"
            "3. Untagged Invoices as on {date}\n"
            "4. Below 1k Invoices as on {date}\n\n"
            "The ageing with the GST invoice numbers, the Unapplied Receipts report and the Untagged Invoices "
            "report are attached."
        ),
        placeholders=("date", "total", "found", "blank"),
    ),
    "rdc-sales-register-report": MailSpec(
        app_key="rdc-sales-register-report",
        label="RDC Sales Register Report",
        default_subject="RDC Sales Register Report: {from_date} to {to_date}",
        default_intro=(
            "Dear Team,\n\n"
            "Please find attached the RDC Sales Register Report for {from_date} to {to_date} ({period}), "
            "{rows} rows."
        ),
        placeholders=("date", "period", "from_date", "to_date", "rows"),
    ),
    "ultrafine-cashflow": MailSpec(
        app_key="ultrafine-cashflow",
        label="Ultrafine Cashflow",
        default_subject="UF - Cash Flow as of {date}",
        default_intro=(
            "Dear All,\n\n"
            "Please find the cash flow details and summary as of {date_plain}."
        ),
        placeholders=("date", "date_plain", "month"),
        scheduled=False,
    ),
}


@dataclass
class MailPayload:
    """What an application's builder hands back for the shared sender."""

    values: dict[str, str]                       # placeholder values for subject / intro
    sections: list[tuple[str, str]] = field(default_factory=list)   # (heading, html) shown under the intro
    css: list[str] = field(default_factory=list)  # class rules the section tables use (kept in <head>)
    downloads: list[tuple[str, str]] = field(default_factory=list)   # (label, path) of the full report, for the preview
    attachment_groups: list[list[str]] = field(default_factory=list)  # one mail per group
    notes: list[str] = field(default_factory=list)                    # shown after the sections
    sender_notes: list[str] = field(default_factory=list)             # for the person sending (preview panel), never in the mail
    intro_note: str = ""                         # a bold line straight after the mail text
    detail: dict = field(default_factory=dict)   # stored with the run
    hold_reason: str | None = None               # set: do not send, record why
    cleanup: list[str] = field(default_factory=list)


# ── validation helpers ───────────────────────────────────────────────────────

def parse_days(raw: str | None) -> list[int]:
    days = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            days.add(int(part))
    return sorted(days)


def format_days(days) -> str:
    return ",".join(str(day) for day in sorted({int(day) for day in days}))


MAX_TIMES = 6
LAST_DAY = 0    # in a list of days of the month: the last day, whatever the month's length


def parse_month_days(raw: str | None) -> list[int]:
    """Days of the month, 1-31, and 0 for the last day."""
    days = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 31:
            days.add(int(part))
    return sorted(days)


def parse_times(row) -> list[str]:
    """The times of day (IST) the mail is due, earliest first."""
    found = {part.strip() for part in (row.send_times or "").split(",") if _TIME_RE.fullmatch(part.strip())}
    if not found and row.send_time and _TIME_RE.fullmatch(row.send_time):
        found = {row.send_time}
    return sorted(found)


def runs_on(row, day: date) -> bool:
    """Whether the schedule has the mail go out on ``day``."""
    if (row.send_mode or "weekly") == "monthly":
        days = parse_month_days(row.send_month_days)
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day in days or (LAST_DAY in days and day.day == last)
    return day.weekday() in parse_days(row.send_days)


def _slot_key(day: date, time_of_day: str) -> str:
    return f"{day.isoformat()} {time_of_day}"


def _attempted_through(row) -> str:
    """The last slot ("YYYY-MM-DD HH:MM") already started; every slot up to it is done. A row that only
    knows the day of its last attempt counts the whole of that day as done."""
    if row.last_attempt_slot:
        return row.last_attempt_slot
    if row.last_attempt_date:
        return _slot_key(row.last_attempt_date, "23:59")
    return ""


def validate_template(template: str, allowed: tuple[str, ...]) -> None:
    try:
        fields = {
            name.split(".", 1)[0].split("[", 1)[0]
            for _, name, _, _ in Formatter().parse(template)
            if name is not None
        }
    except ValueError as exc:
        raise ValueError("The text has an unmatched { or }") from exc
    unsupported = sorted(fields - set(allowed))
    if unsupported:
        shown = ", ".join("{" + name + "}" for name in allowed)
        raise ValueError(f"{{{unsupported[0]}}} is not available here. You can use: {shown}")


def render_text(template: str, values: dict[str, str]) -> str:
    return template.format_map(values)


# ── settings ─────────────────────────────────────────────────────────────────

# Body texts an app shipped with earlier, saved as the settings when the row was first created. A row that
# still holds one word for word never had its text edited, so it moves to the current default (an edited
# text is left alone).
SUPERSEDED_INTROS: dict[str, tuple[str, ...]] = {
    "debtors-reports-auto-mailer": (
        "Dear Team,\n\n"
        "Please find below:\n\n"
        "1. Ageing with GST invoice numbers as on {date} ({found} of {total} rows have a GST number)\n"
        "2. Unapplied Receipts as on {date}\n"
        "3. Untagged Invoices as on {date}\n"
        "4. Below 1k Invoices as on {date}\n\n"
        "The ageing with the GST invoice numbers, the Unapplied Receipts report and the Untagged Invoices "
        "report are attached.\n\n"
        "Kindly clear the same on priority basis and confirm.",
    ),
}


def _retire_superseded_intro(db: Session, row: ScheduledReportMail) -> None:
    old_texts = SUPERSEDED_INTROS.get(row.app_key, ())
    if (row.intro_template or "").replace("\r\n", "\n").strip() in old_texts:
        row.intro_template = SPECS[row.app_key].default_intro
        db.commit()


def get_or_create_settings(db: Session, app_key: str) -> ScheduledReportMail:
    spec = SPECS[app_key]
    row = db.query(ScheduledReportMail).filter(ScheduledReportMail.app_key == app_key).first()
    if row is None:
        row = ScheduledReportMail(
            app_key=app_key,
            enabled=False,
            send_days=format_days(DEFAULT_SEND_DAYS),
            send_time=DEFAULT_SEND_TIME,
            subject_template=spec.default_subject,
            intro_template=spec.default_intro,
            signature="",
            send_when_unmapped=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    elif row.is_deleted:
        row.is_deleted = False
        db.commit()
    _retire_superseded_intro(db, row)
    return row


def _is_running(row: ScheduledReportMail, now_utc: datetime) -> bool:
    return bool(row.running_since and now_utc - row.running_since < _RUN_STALE_AFTER)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def next_run(row: ScheduledReportMail, now: datetime | None = None) -> datetime | None:
    """When the next automatic mail is due (IST), or None when switched off."""
    times = parse_times(row)
    if not row.enabled or not times:
        return None
    now = now or now_ist()
    done = _attempted_through(row)
    for offset in range(0, 100):
        day = now.date() + timedelta(days=offset)
        if not runs_on(row, day):
            continue
        for time_of_day in times:
            if _slot_key(day, time_of_day) <= done:
                continue
            hour, minute = (int(part) for part in time_of_day.split(":"))
            due = datetime.combine(day, dtime(hour, minute), tzinfo=now.tzinfo)
            # a slot of today counts until it has been attempted; once its time
            # has passed it is due right now
            return max(due, now) if offset == 0 else due
    return None


# ── who sends it ─────────────────────────────────────────────────────────────

def _person_name(user: User) -> str:
    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return name or user.email


def sender_options(db: Session) -> list[dict]:
    """The people whose own email (Settings) can send the automatic mail: active
    users who have set one up."""
    rows = (
        db.query(User, EmailSettings)
        .join(EmailSettings, EmailSettings.user_id == User.id)
        .filter(
            User.is_deleted.is_(False), User.is_active.is_(True), EmailSettings.is_deleted.is_(False),
        )
        .all()
    )
    options = [
        {"user_id": user.id, "name": _person_name(user), "email": settings.sender_email}
        for user, settings in rows
        if settings.sender_email and settings.app_password_encrypted
    ]
    return sorted(options, key=lambda option: option["name"].lower())


def resolve_sender(db: Session, sender_user_id: int | None) -> dict:
    """{configured, email, app_password, problem} of whoever sends the automatic mail:
    the chosen person's own email, or the system email when none is chosen."""
    if sender_user_id is None:
        system = get_system_email_settings(db)
        return {
            "configured": bool(system["configured"]),
            "email": system.get("sender_email"),
            "app_password": system.get("app_password"),
            "problem": None if system["configured"] else (
                "The system email is not set up yet. Ask an administrator to configure it under "
                "Email administration, then try again."
            ),
        }
    row = (
        db.query(User, EmailSettings)
        .join(EmailSettings, EmailSettings.user_id == User.id)
        .filter(User.id == sender_user_id, User.is_deleted.is_(False), User.is_active.is_(True),
                EmailSettings.is_deleted.is_(False))
        .first()
    )
    if row is None or not row[1].sender_email or not row[1].app_password_encrypted:
        return {
            "configured": False, "email": None, "app_password": None,
            "problem": (
                "The person chosen to send this mail no longer has an email sender set up (or is no longer "
                "an active user). Choose who it is sent from again under the automatic mail settings."
            ),
        }
    return {
        "configured": True,
        "email": row[1].sender_email,
        "app_password": security.decrypt(row[1].app_password_encrypted),
        "problem": None,
    }


def manual_sender_options(db: Session, user: User) -> list[dict]:
    """Who a mail sent by hand can go from: the signed-in person's own email, the system email (for
    example no-reply@...), and every other person who has set up their own."""
    own = (
        db.query(EmailSettings)
        .filter(EmailSettings.user_id == user.id, EmailSettings.is_deleted.is_(False)).first()
    )
    own_email = own.sender_email if own and own.sender_email and own.app_password_encrypted else None
    system = get_system_email_settings(db)
    options = [
        {"value": "me", "label": "Me", "email": own_email, "configured": own_email is not None},
        {"value": "system", "label": "System email", "email": system.get("sender_email"),
         "configured": bool(system["configured"])},
    ]
    options += [
        {"value": str(person["user_id"]), "label": person["name"], "email": person["email"], "configured": True}
        for person in sender_options(db) if person["user_id"] != user.id
    ]
    return options


def resolve_manual_sender(db: Session, choice: str | None, user_id: int) -> dict:
    """{email, app_password, signature, configured, problem} for a mail sent by hand. ``choice`` is "me"
    (or empty), "system", or the id of a person who has set up their own email."""
    choice = (choice or "me").strip()
    if choice == "me":
        own = (
            db.query(EmailSettings)
            .filter(EmailSettings.user_id == user_id, EmailSettings.is_deleted.is_(False)).first()
        )
        if own is None or not own.sender_email or not own.app_password_encrypted:
            return {"email": None, "app_password": None, "signature": "", "configured": False,
                    "problem": "You haven't set up your email sender yet - go to Settings."}
        return {"email": own.sender_email, "app_password": security.decrypt(own.app_password_encrypted),
                "signature": own.signature or "", "configured": True, "problem": None}
    if choice == "system":
        sender = resolve_sender(db, None)
    elif choice.isdigit():
        sender = resolve_sender(db, int(choice))
    else:
        return {"email": None, "app_password": None, "signature": "", "configured": False,
                "problem": "Choose who the mail is sent from from the list."}
    return {"email": sender["email"], "app_password": sender["app_password"], "signature": "",
            "configured": sender["configured"], "problem": sender["problem"]}


def settings_view(db: Session, app_key: str) -> dict:
    spec = SPECS[app_key]
    row = get_or_create_settings(db, app_key)
    recipients = get_report_recipient_defaults(db, app_key)
    upcoming = next_run(row)
    sender = resolve_sender(db, row.sender_user_id)
    try:
        detail = json.loads(row.last_detail) if row.last_detail else None
    except ValueError:
        detail = None
    return {
        "enabled": bool(row.enabled),
        "mode": row.send_mode or "weekly",
        "days": parse_days(row.send_days),
        "month_days": parse_month_days(row.send_month_days),
        "time": row.send_time,
        "times": parse_times(row),
        "to": recipients["to"],
        "cc": recipients["cc"],
        "subject_template": row.subject_template,
        "intro_template": row.intro_template,
        "signature": row.signature or "",
        "send_when_unmapped": bool(row.send_when_unmapped),
        "placeholders": list(spec.placeholders),
        "default_subject": spec.default_subject,
        "default_intro": spec.default_intro,
        "sender_user_id": row.sender_user_id,
        "sender_options": sender_options(db),
        "sender_email": sender["email"],
        "sender_configured": sender["configured"],
        "sender_problem": sender["problem"],
        "system_sender_email": get_system_email_settings(db).get("sender_email"),
        "next_run": upcoming.isoformat() if upcoming else None,
        "running": _is_running(row, _utcnow()),
        "last": {
            "at": row.last_run_at.replace(tzinfo=UTC).isoformat() if row.last_run_at else None,
            "trigger": row.last_trigger,
            "status": row.last_status,
            "error": row.last_error,
            "detail": detail,
        },
    }


def save_settings(db: Session, app_key: str, data: dict) -> dict:
    """Validate and store the admin's choices; ValueError carries a plain message."""
    spec = SPECS[app_key]
    mode = str(data.get("mode") or "weekly")
    if mode not in ("weekly", "monthly"):
        raise ValueError("Choose whether the mail goes out weekly or monthly")
    days = sorted({int(day) for day in data.get("days", []) if isinstance(day, int) and 0 <= day <= 6})
    month_days = sorted({
        int(day) for day in data.get("month_days") or []
        if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 31
    })
    raw_times = data.get("times") or [data.get("time", "")]
    times = sorted({str(value).strip() for value in raw_times})
    if not times or any(not _TIME_RE.fullmatch(value) for value in times):
        raise ValueError("Enter each time as HH:MM, for example 08:30")
    if len(times) > MAX_TIMES:
        raise ValueError(f"At most {MAX_TIMES} send times a day")
    send_time = times[0]
    enabled = bool(data.get("enabled"))
    if enabled and mode == "weekly" and not days:
        raise ValueError("Pick at least one day of the week, or switch automatic mail off")
    if enabled and mode == "monthly" and not month_days:
        raise ValueError("Pick at least one day of the month, or switch automatic mail off")

    subject = str(data.get("subject_template", "")).strip()
    intro = str(data.get("intro_template", "")).strip()
    if not subject:
        raise ValueError("The subject cannot be empty")
    if len(subject) > 300:
        raise ValueError("The subject is too long")
    if "\n" in subject or "\r" in subject:
        raise ValueError("The subject must be a single line")
    if not intro:
        raise ValueError("The mail text cannot be empty")
    validate_template(subject, spec.placeholders)
    validate_template(intro, spec.placeholders)

    to_list = [normalize_email(value) for value in data.get("to", []) if str(value).strip()]
    cc_list = [normalize_email(value) for value in data.get("cc", []) if str(value).strip()]
    if enabled and not to_list:
        raise ValueError("Add at least one To recipient, or switch automatic mail off")

    raw_sender = data.get("sender_user_id")
    sender_user_id = None
    if raw_sender not in (None, ""):
        if isinstance(raw_sender, bool) or not isinstance(raw_sender, int):
            raise ValueError("Choose who the mail is sent from from the list")
        if not resolve_sender(db, raw_sender)["configured"]:
            raise ValueError(
                "That person has no email sender set up (Settings -> Email), so the mail cannot go out from them"
            )
        sender_user_id = raw_sender

    row = get_or_create_settings(db, app_key)
    # recipients first: it raises on a duplicate address before anything else is touched
    set_report_recipient_defaults(db, app_key, to_list, cc_list)
    row.sender_user_id = sender_user_id
    row.enabled = enabled
    row.send_mode = mode
    row.send_days = format_days(days)
    row.send_month_days = ",".join(str(day) for day in month_days) or None
    row.send_time = send_time
    row.send_times = ",".join(times) if len(times) > 1 else None
    row.subject_template = subject
    row.intro_template = intro
    row.signature = str(data.get("signature", "")).strip()
    row.send_when_unmapped = bool(data.get("send_when_unmapped", True))
    # Saving must never fire a surprise mail to everyone: if today's time has
    # already passed, the first automatic mail is the next slot, not "now".
    now = now_ist()
    passed = [value for value in times if value <= now.strftime("%H:%M")]
    if enabled and passed:
        row.last_attempt_date = now.date()
        row.last_attempt_slot = _slot_key(now.date(), max(passed))
    row.updated_at = datetime.utcnow()
    db.commit()
    audit_middleware.log_event("scheduled_mail.settings_saved", details={
        "app_key": app_key, "enabled": enabled, "mode": mode, "days": days, "month_days": month_days,
        "times": times,
    })
    return settings_view(db, app_key)


# ── running ──────────────────────────────────────────────────────────────────

def _claim_run(app_key: str, trigger: str, requested_by: str | None) -> dict | None:
    """Mark the run as started and open its log entry; None (after logging a
    "skipped" entry) when another run is still in progress."""
    db = SessionLocal()
    try:
        row = (
            db.query(ScheduledReportMail)
            .filter(ScheduledReportMail.app_key == app_key)
            .with_for_update()
            .first()
        ) or get_or_create_settings(db, app_key)
        _retire_superseded_intro(db, row)
        now = _utcnow()
        if _is_running(row, now):
            db.add(ScheduledReportMailLog(
                app_key=app_key, trigger=trigger, requested_by=requested_by, status="skipped",
                started_at=now, finished_at=now,
                error_message="Not run: an earlier run of this mail was still in progress.",
            ))
            db.commit()
            return None
        row.running_since = now
        entry = ScheduledReportMailLog(
            app_key=app_key, trigger=trigger, requested_by=requested_by, status="running", started_at=now,
        )
        db.add(entry)
        db.commit()
        return {
            "subject_template": row.subject_template,
            "intro_template": row.intro_template,
            "signature": row.signature or "",
            "send_when_unmapped": bool(row.send_when_unmapped),
            "sender_user_id": row.sender_user_id,
            "log_id": entry.id,
        }
    finally:
        db.close()


def _finish_run(app_key: str, trigger: str, status: str, error: str | None, detail: dict,
                log_id: int, messages: list[dict], recipients: dict | None) -> None:
    db = SessionLocal()
    try:
        now = _utcnow()
        row = get_or_create_settings(db, app_key)
        row.running_since = None
        row.last_run_at = now
        row.last_trigger = trigger
        row.last_status = status
        row.last_error = (error or None) and error[:2000]
        row.last_detail = json.dumps(detail, ensure_ascii=False, default=str)
        entry = db.get(ScheduledReportMailLog, log_id)
        if entry is not None:
            entry.status = status
            entry.finished_at = now
            entry.error_message = (error or None) and error[:2000]
            entry.detail = json.dumps(detail, ensure_ascii=False, default=str)
            entry.messages = json.dumps(messages, ensure_ascii=False)
            if recipients is not None:
                entry.to_recipients = json.dumps(recipients.get("to", []))
                entry.cc_recipients = json.dumps(recipients.get("cc", []))
        db.commit()
    finally:
        db.close()


def record_manual_send(app_key: str, requested_by: str | None, started_at: datetime, status: str,
                       error: str | None, recipients: dict, messages: list[dict], detail: dict) -> None:
    """Log a mail that a person previewed and sent by hand (not through
    run_mail_job), so the history shows every mail whichever way it left."""
    db = SessionLocal()
    try:
        db.add(ScheduledReportMailLog(
            app_key=app_key, trigger="manual", requested_by=requested_by, status=status,
            started_at=started_at, finished_at=_utcnow(),
            to_recipients=json.dumps(recipients.get("to", [])), cc_recipients=json.dumps(recipients.get("cc", [])),
            messages=json.dumps(messages, ensure_ascii=False),
            detail=json.dumps(detail, ensure_ascii=False, default=str),
            error_message=(error or None) and error[:2000],
        ))
        db.commit()
    finally:
        db.close()


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=UTC).isoformat() if value else None


def log_view(db: Session, app_key: str, limit: int = 20, offset: int = 0) -> dict:
    """The newest runs first. A run still marked "running" long after it can
    possibly have finished is shown as "interrupted" (its worker died)."""
    query = db.query(ScheduledReportMailLog).filter(
        ScheduledReportMailLog.app_key == app_key, ScheduledReportMailLog.is_deleted.is_(False),
    )
    total = query.count()
    rows = (
        query.order_by(ScheduledReportMailLog.started_at.desc(), ScheduledReportMailLog.id.desc())
        .offset(max(offset, 0)).limit(max(1, min(limit, 100))).all()
    )
    now = _utcnow()

    def loads(raw, default):
        try:
            return json.loads(raw) if raw else default
        except ValueError:
            return default

    items = []
    for row in rows:
        status = row.status
        if status == "running" and now - row.started_at >= _RUN_STALE_AFTER:
            status = "interrupted"
        items.append({
            "id": row.id,
            "trigger": row.trigger,
            "requested_by": row.requested_by,
            "status": status,
            "started_at": _iso(row.started_at),
            "finished_at": _iso(row.finished_at),
            "to": loads(row.to_recipients, []),
            "cc": loads(row.cc_recipients, []),
            "messages": loads(row.messages, []),
            "detail": loads(row.detail, {}),
            "error": row.error_message,
        })
    return {"total": total, "items": items}


def _wrap_html(intro_text: str, sections: list[tuple[str, str]], notes: list[str], signature: str,
               css: list[str] | None = None, intro_note: str = "") -> str:
    parts = [_plain_to_html(intro_text)]
    if intro_note:
        parts.append('<p style="margin:0 0 12px 0;"><b>' + html.escape(intro_note) + "</b></p>")
    for heading, table_html in sections:
        title = f'<p style="margin:14px 0 4px 0;"><b>{html.escape(heading)}</b></p>\n' if heading else ""
        parts.append(f"{title}{table_html}")
    for note in notes:
        parts.append(
            '<p style="margin:12px 0 0 0;color:#7a4b00;"><i>' + html.escape(note) + "</i></p>"
        )
    if signature.strip():
        escaped = html.escape(signature.strip()).replace("\r\n", "\n").replace("\r", "\n")
        parts.append(
            '<p style="margin-top:24px;">' + "<br>".join(escaped.split("\n")) + "</p>"
        )
    head = '<head><meta charset="utf-8">' + ("<style>" + "".join(css) + "</style>" if css else "") + "</head>\n"
    return (
        "<html>\n" + head + '<body style="font-family:Calibri,Arial,sans-serif;font-size:12pt;'
        'color:#1a1a1a;line-height:1.65;">\n\n' + "\n\n".join(parts) + "\n</body>\n</html>"
    )


def compose_messages(snapshot: dict, payload: MailPayload) -> list[dict]:
    """The mail(s) for a built report: [{subject, html_body, attachments}].
    One mail normally; one per attachment group when the report is split into
    parts (the tables go in the first only)."""
    groups = payload.attachment_groups or [[]]
    messages = []
    for index, group in enumerate(groups, 1):
        subject = render_text(snapshot["subject_template"], payload.values)
        if len(groups) > 1:
            subject = f"{subject} (Part {index} of {len(groups)})"
        notes = list(payload.notes)
        if len(groups) > 1:
            notes.append(
                f"The report is too large for a single email, so it is sent in {len(groups)} parts. "
                f"This is part {index}."
            )
        body = _wrap_html(
            render_text(snapshot["intro_template"], payload.values),
            payload.sections if index == 1 else [],
            notes,
            snapshot["signature"],
            payload.css if index == 1 else None,
            payload.intro_note,
        )
        messages.append({"subject": subject, "html_body": body, "attachments": list(group)})
    return messages


def _deliver(db: Session, app_key: str, subject: str, html_body: str, attachments: list[str],
             sender_user_id: int | None = None) -> dict:
    sender = resolve_sender(db, sender_user_id)
    if not sender["configured"]:
        raise JobUserError(sender["problem"])
    recipients = get_report_recipient_defaults(db, app_key)
    if not recipients["to"]:
        raise JobUserError("There is no To recipient. Add at least one under the automatic mail settings.")
    send_mail(
        from_email=sender["email"],
        app_password=sender["app_password"],
        to_addresses=recipients["to"],
        cc_addresses=recipients["cc"],
        subject=subject,
        html_body=html_body,
        attachments=attachments,
    )
    return recipients


def _recipients_snapshot(app_key: str) -> dict:
    db = SessionLocal()
    try:
        return get_report_recipient_defaults(db, app_key)
    finally:
        db.close()


def attachment_info(paths: list[str]) -> list[dict]:
    info = []
    for path in paths:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        info.append({"name": os.path.basename(path), "bytes": size})
    return info


def run_mail_job(app_key: str, trigger: str, build, progress_cb=None, cancel_event=None,
                 requested_by: str | None = None) -> dict:
    """Build the report with ``build(snapshot, progress_cb, cancel_event) ->
    MailPayload``, mail it to the application's recipients, and record the
    outcome in the settings row and in the send log. ``trigger`` is
    "scheduled" or "manual" (``requested_by`` then names the admin)."""
    snapshot = _claim_run(app_key, trigger, requested_by)
    if snapshot is None:
        raise JobUserError("An automatic mail run for this report is already in progress. Try again in a few minutes.")
    log_id = snapshot["log_id"]
    payload: MailPayload | None = None
    status, error, detail = "failed", None, {}
    sent_messages: list[dict] = []
    recipients_used: dict | None = None
    try:
        payload = build(snapshot, progress_cb, cancel_event)
        detail = dict(payload.detail)
        if payload.hold_reason:
            recipients_used = _recipients_snapshot(app_key)
            status, error = "held", payload.hold_reason
            return {"status": status, "message": payload.hold_reason}

        if progress_cb:
            progress_cb(0.93, "Sending the mail...")
        db = SessionLocal()
        try:
            messages = compose_messages(snapshot, payload)
            for message in messages:
                recipients = recipients_used = _deliver(
                    db, app_key, message["subject"], message["html_body"], message["attachments"],
                    snapshot.get("sender_user_id"),
                )
                sent_messages.append({
                    "subject": message["subject"],
                    "sent_at": _iso(_utcnow()),
                    "attachments": attachment_info(message["attachments"]),
                })
            detail["recipients"] = recipients
            detail["sent_from"] = resolve_sender(db, snapshot.get("sender_user_id"))["email"]
            detail["mails_sent"] = len(messages)
        finally:
            db.close()
        status = "sent"
        audit_middleware.log_event("scheduled_mail.sent", details={"app_key": app_key, "trigger": trigger})
        if progress_cb:
            progress_cb(1.0, "Mail sent")
        return {"status": status, "detail": detail}
    except JobCancelled:
        error = "Cancelled before the mail was sent."
        raise
    except Exception as exc:  # noqa: BLE001 - recorded for the administrator below
        logger.exception("Scheduled report mail failed (%s, %s)", app_key, trigger)
        error = str(exc)
        audit_middleware.log_event("scheduled_mail.failed", details={"app_key": app_key, "trigger": trigger, "error": type(exc).__name__})
        if isinstance(exc, JobUserError):
            raise
        raise JobUserError(f"The automatic mail could not be sent: {exc}") from exc
    finally:
        if payload is not None:
            for path in payload.cleanup:
                if Path(path).is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    Path(path).unlink(missing_ok=True)
        _finish_run(app_key, trigger, status, error, detail, log_id, sent_messages, recipients_used)


# ── scheduler hook ───────────────────────────────────────────────────────────

def _job_for(app_key: str):
    # imported here: these modules import this one
    if app_key == "untagged-invoices-report":
        from app.services.untagged_invoices.auto_mail import run_auto_mail_job
    elif app_key == "debtors-reports-auto-mailer":
        from app.services.debtors_mailer.auto_mail import run_auto_mail_job
    elif app_key == "rdc-sales-register-report":
        from app.services.sales_register.auto_mail import run_auto_mail_job
    else:
        from app.services.gst_invoice_adder.auto_mail import run_auto_mail_job
    return run_auto_mail_job


def due_slot(row: ScheduledReportMail, now: datetime) -> str | None:
    """The latest slot of today that is due and not yet started, or None. If the
    scheduler was down across several slots, the mail goes out once."""
    if not row.enabled or row.is_deleted or not runs_on(row, now.date()):
        return None
    done = _attempted_through(row)
    now_key = now.strftime("%Y-%m-%d %H:%M")
    due = [
        key for key in (_slot_key(now.date(), value) for value in parse_times(row))
        if done < key <= now_key
    ]
    return max(due) if due else None


def due_now(row: ScheduledReportMail, now: datetime) -> bool:
    return due_slot(row, now) is not None


def enqueue_due_checks() -> list[str]:
    """Called by the scheduler loop. Starts the background job for every
    application whose mail is due, once per scheduled slot."""
    from app.jobs import submit_job

    started: list[str] = []
    for app_key, spec in SPECS.items():
        if not spec.scheduled:
            continue
        db = SessionLocal()
        try:
            row = (
                db.query(ScheduledReportMail)
                .filter(ScheduledReportMail.app_key == app_key)
                .with_for_update()
                .first()
            )
            now = now_ist()
            slot = due_slot(row, now) if row is not None else None
            if slot is None:
                continue
            row.last_attempt_date = now.date()
            row.last_attempt_slot = slot
            db.commit()
        finally:
            db.close()
        submit_job(_job_for(app_key), "scheduled", owner_id=0, cancel_on_disconnect=False)
        started.append(app_key)
    return started
