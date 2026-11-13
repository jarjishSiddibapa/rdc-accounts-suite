"""Meeting Debtors Reports Auto Mailer: the Debtors Reports Auto Mailer (the GST-numbered ageing, the
Unapplied Receipts report and the Untagged Invoices report, mailed together) with one difference - the
date the reports are as on is chosen (a calendar date, or the last day of last month; see as_on.py)
instead of always being today. Every report, and every figure in the mail, is as it stood on that date.

Everything else is the Debtors Reports Auto Mailer: the same builder (app.services.debtors_mailer.auto_mail),
the same mapping tables, the same wording and the same way of sending (always from the system email).
It has its own schedule, recipients and send log.
"""

from __future__ import annotations

from app.jobs import JobUserError
from app.services import report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services.debtors_mailer import auto_mail as debtors
from app.services.meeting_debtors import as_on as as_on_choice

APP_KEY = "meeting-debtors-reports-auto-mailer"


def build(snapshot: dict, progress_cb=None, cancel_event=None) -> mail.MailPayload:
    try:
        mode, as_on = as_on_choice.current(APP_KEY)
    except as_on_choice.AsOnError as exc:
        raise JobUserError(str(exc)) from exc
    payload = debtors.build(snapshot, progress_cb, cancel_event, as_on=as_on)
    payload.detail["as_on_mode"] = mode
    return payload


def run_auto_mail_job(trigger: str = "scheduled", requested_by: str | None = None,
                      progress_cb=None, cancel_event=None) -> dict:
    return mail.run_mail_job(
        APP_KEY, trigger, build, progress_cb=progress_cb, cancel_event=cancel_event, requested_by=requested_by,
    )


def run_preview_job(user_id: int, to: list, cc: list, subject: str, intro: str,
                    allow_unmapped: bool = False, sender: str | None = None,
                    progress_cb=None, cancel_event=None) -> dict:
    """Build the reports and the exact mail for review. Sends nothing."""
    return report_mail_preview.build_preview(
        APP_KEY, build, user_id, to, cc, subject, intro, allow_unmapped, sender=sender,
        progress_cb=progress_cb, cancel_event=cancel_event,
    )
