"""Automatic report mail, shared by the Untagged Invoices Report and the GST
Invoice Number Adder.

An administrator chooses, per application, whether the report is mailed
automatically, on which weekdays and at what time (IST), the wording, and
whether to hold the mail when mappings are missing. The To / Cc recipients are
the application's admin-managed defaults (the same list shown under Admin ->
Report recipients). "Send now" runs the very same job on demand.

The mail goes out through the system email identity (Admin -> Email
settings): a scheduled run has no signed-in user whose own sender could be
used.

The scheduler process calls ``enqueue_due_checks`` every ~45 seconds; the
report itself is built and mailed by a durable background job
(``run_mail_job``), so a restart or a closed browser never loses a run.
"""

from __future__ import annotations

import html
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dtime, timedelta
from pathlib import Path
from string import Formatter

from sqlalchemy.orm import Session

from app import audit_middleware
from app.database import SessionLocal
from app.jobs import JobCancelled, JobUserError
from app.models import ScheduledReportMail
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
}


@dataclass
class MailPayload:
    """What an application's builder hands back for the shared sender."""

    values: dict[str, str]                       # placeholder values for subject / intro
    sections: list[tuple[str, str]] = field(default_factory=list)   # (heading, html) shown under the intro
    css: list[str] = field(default_factory=list)  # class rules the section tables use (kept in <head>)
    attachment_groups: list[list[str]] = field(default_factory=list)  # one mail per group
    notes: list[str] = field(default_factory=list)                    # shown after the sections
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
    return row


def _is_running(row: ScheduledReportMail, now_utc: datetime) -> bool:
    return bool(row.running_since and now_utc - row.running_since < _RUN_STALE_AFTER)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def next_run(row: ScheduledReportMail, now: datetime | None = None) -> datetime | None:
    """When the next automatic mail is due (IST), or None when switched off."""
    days = parse_days(row.send_days)
    if not row.enabled or not days:
        return None
    now = now or now_ist()
    hour, minute = (int(part) for part in row.send_time.split(":"))
    for offset in range(0, 8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        due = datetime.combine(day, dtime(hour, minute), tzinfo=now.tzinfo)
        if offset == 0:
            # today's slot counts until it has been attempted; once its time
            # has passed it is due right now
            if row.last_attempt_date == day:
                continue
            return max(due, now)
        return due
    return None


def settings_view(db: Session, app_key: str) -> dict:
    spec = SPECS[app_key]
    row = get_or_create_settings(db, app_key)
    recipients = get_report_recipient_defaults(db, app_key)
    upcoming = next_run(row)
    try:
        detail = json.loads(row.last_detail) if row.last_detail else None
    except ValueError:
        detail = None
    return {
        "enabled": bool(row.enabled),
        "days": parse_days(row.send_days),
        "time": row.send_time,
        "to": recipients["to"],
        "cc": recipients["cc"],
        "subject_template": row.subject_template,
        "intro_template": row.intro_template,
        "signature": row.signature or "",
        "send_when_unmapped": bool(row.send_when_unmapped),
        "placeholders": list(spec.placeholders),
        "default_subject": spec.default_subject,
        "default_intro": spec.default_intro,
        "sender_configured": bool(get_system_email_settings(db)["configured"]),
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
    days = sorted({int(day) for day in data.get("days", []) if isinstance(day, int) and 0 <= day <= 6})
    send_time = str(data.get("time", "")).strip()
    if not _TIME_RE.fullmatch(send_time):
        raise ValueError("Enter the time as HH:MM, for example 08:30")
    enabled = bool(data.get("enabled"))
    if enabled and not days:
        raise ValueError("Pick at least one day of the week, or switch automatic mail off")

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

    row = get_or_create_settings(db, app_key)
    # recipients first: it raises on a duplicate address before anything else is touched
    set_report_recipient_defaults(db, app_key, to_list, cc_list)
    row.enabled = enabled
    row.send_days = format_days(days)
    row.send_time = send_time
    row.subject_template = subject
    row.intro_template = intro
    row.signature = str(data.get("signature", "")).strip()
    row.send_when_unmapped = bool(data.get("send_when_unmapped", True))
    # Saving must never fire a surprise mail to everyone: if today's time has
    # already passed, the first automatic mail is the next slot, not "now".
    now = now_ist()
    if enabled and now.strftime("%H:%M") >= send_time:
        row.last_attempt_date = now.date()
    row.updated_at = datetime.utcnow()
    db.commit()
    audit_middleware.log_event("scheduled_mail.settings_saved", details={
        "app_key": app_key, "enabled": enabled, "days": days, "time": send_time,
    })
    return settings_view(db, app_key)


# ── running ──────────────────────────────────────────────────────────────────

def _claim_run(app_key: str, trigger: str) -> dict | None:
    """Mark the run as started; None when another run is still in progress."""
    db = SessionLocal()
    try:
        row = (
            db.query(ScheduledReportMail)
            .filter(ScheduledReportMail.app_key == app_key)
            .with_for_update()
            .first()
        ) or get_or_create_settings(db, app_key)
        now = _utcnow()
        if _is_running(row, now):
            return None
        row.running_since = now
        db.commit()
        return {
            "subject_template": row.subject_template,
            "intro_template": row.intro_template,
            "signature": row.signature or "",
            "send_when_unmapped": bool(row.send_when_unmapped),
        }
    finally:
        db.close()


def _finish_run(app_key: str, trigger: str, status: str, error: str | None, detail: dict) -> None:
    db = SessionLocal()
    try:
        row = get_or_create_settings(db, app_key)
        row.running_since = None
        row.last_run_at = _utcnow()
        row.last_trigger = trigger
        row.last_status = status
        row.last_error = (error or None) and error[:2000]
        row.last_detail = json.dumps(detail, ensure_ascii=False, default=str)
        db.commit()
    finally:
        db.close()


def _wrap_html(intro_text: str, sections: list[tuple[str, str]], notes: list[str], signature: str,
               css: list[str] | None = None) -> str:
    parts = [_plain_to_html(intro_text)]
    for heading, table_html in sections:
        parts.append(f'<p style="margin:14px 0 4px 0;"><b>{html.escape(heading)}</b></p>\n{table_html}')
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


def _deliver(db: Session, app_key: str, subject: str, html_body: str, attachments: list[str]) -> dict:
    sender = get_system_email_settings(db)
    if not sender["configured"]:
        raise JobUserError(
            "The system email is not set up yet. Ask an administrator to configure it under "
            "Email administration, then try again."
        )
    recipients = get_report_recipient_defaults(db, app_key)
    if not recipients["to"]:
        raise JobUserError("There is no To recipient. Add at least one under the automatic mail settings.")
    send_mail(
        from_email=sender["sender_email"],
        app_password=sender["app_password"],
        to_addresses=recipients["to"],
        cc_addresses=recipients["cc"],
        subject=subject,
        html_body=html_body,
        attachments=attachments,
    )
    return recipients


def run_mail_job(app_key: str, trigger: str, build, progress_cb=None, cancel_event=None) -> dict:
    """Build the report with ``build(snapshot, progress_cb, cancel_event) ->
    MailPayload``, mail it to the application's recipients, and record the
    outcome. ``trigger`` is "scheduled" or "manual"."""
    snapshot = _claim_run(app_key, trigger)
    if snapshot is None:
        raise JobUserError("An automatic mail run for this report is already in progress. Try again in a few minutes.")
    payload: MailPayload | None = None
    status, error, detail = "failed", None, {}
    try:
        payload = build(snapshot, progress_cb, cancel_event)
        detail = dict(payload.detail)
        if payload.hold_reason:
            status, error = "held", payload.hold_reason
            return {"status": status, "message": payload.hold_reason}

        if progress_cb:
            progress_cb(0.93, "Sending the mail...")
        db = SessionLocal()
        try:
            groups = payload.attachment_groups or [[]]
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
                )
                recipients = _deliver(db, app_key, subject, body, group)
            detail["recipients"] = recipients
            detail["mails_sent"] = len(groups)
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
        _finish_run(app_key, trigger, status, error, detail)


# ── scheduler hook ───────────────────────────────────────────────────────────

def _job_for(app_key: str):
    # imported here: these modules import this one
    if app_key == "untagged-invoices-report":
        from app.services.untagged_invoices.auto_mail import run_auto_mail_job
    else:
        from app.services.gst_invoice_adder.auto_mail import run_auto_mail_job
    return run_auto_mail_job


def due_now(row: ScheduledReportMail, now: datetime) -> bool:
    return (
        bool(row.enabled)
        and not row.is_deleted
        and now.weekday() in parse_days(row.send_days)
        and now.strftime("%H:%M") >= row.send_time
        and row.last_attempt_date != now.date()
    )


def enqueue_due_checks() -> list[str]:
    """Called by the scheduler loop. Starts the background job for every
    application whose mail is due, at most once per calendar day."""
    from app.jobs import submit_job

    started: list[str] = []
    for app_key in SPECS:
        db = SessionLocal()
        try:
            row = (
                db.query(ScheduledReportMail)
                .filter(ScheduledReportMail.app_key == app_key)
                .with_for_update()
                .first()
            )
            now = now_ist()
            if row is None or not due_now(row, now):
                continue
            row.last_attempt_date = now.date()
            db.commit()
        finally:
            db.close()
        submit_job(_job_for(app_key), "scheduled", owner_id=0, cancel_on_disconnect=False)
        started.append(app_key)
    return started
