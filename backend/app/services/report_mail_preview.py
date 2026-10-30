"""Build a report mail for review - and send it only when a person confirms.

The same two-step flow as the Unaccounted / MRN / PO mail: ``build_preview``
generates the report and composes the exact mail(s) that would go out
(subject, body, recipients, attachments) but sends nothing; a separate
confirm-send request (see app.routers.report_mail_routes) is what dispatches
the stored result, from the signed-in person's own email identity.
"""

from __future__ import annotations

import os

from app.database import SessionLocal
from app.services import scheduled_report_mail as mail
from app.services.mailer_shared import get_email_settings

_SETTINGS_HINT = "You haven't set up your email sender yet - go to Settings."


def _file_entry(path: str) -> dict:
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    return {"path": path, "name": os.path.basename(path), "bytes": size}


def build_preview(app_key: str, build, user_id: int, to: list[str], cc: list[str], subject: str,
                  intro: str, allow_unmapped: bool = False, sender: str | None = None,
                  progress_cb=None, cancel_event=None) -> dict:
    """Result statuses: ``preview`` (ready to confirm), ``needs_mapping_fix``
    (mappings are missing and ``allow_unmapped`` is off) or
    ``email_not_configured``. ``sender`` (an application that lets the person
    choose): "me" (the default), "system" or a person's id; see
    scheduled_report_mail.resolve_manual_sender."""
    if sender is None:
        # the person's own email, as every application did before a sender could be chosen
        sender_info = get_email_settings(user_id)
        if not sender_info["configured"]:
            return {"status": "email_not_configured", "message": _SETTINGS_HINT}
    else:
        db = SessionLocal()
        try:
            sender_info = mail.resolve_manual_sender(db, sender, user_id)
        finally:
            db.close()
        if not sender_info["configured"]:
            return {"status": "email_not_configured", "message": sender_info["problem"] or _SETTINGS_HINT}

    snapshot = {
        "subject_template": subject,
        "intro_template": intro,
        "signature": sender_info["signature"],
        "send_when_unmapped": allow_unmapped,
    }
    payload = build(snapshot, progress_cb, cancel_event)
    if payload.hold_reason:
        return {
            "status": "needs_mapping_fix",
            "message": payload.hold_reason,
            "validation_warnings": payload.detail.get("unmapped") or [],
        }

    messages = []
    for message in mail.compose_messages(snapshot, payload):
        messages.append({
            "subject": message["subject"],
            "html_body": message["html_body"],
            "attachments": [_file_entry(path) for path in message["attachments"]],
        })
    return {
        "status": "preview",
        "from_email": sender_info["email"],
        "sender": sender or "me",
        "to": to,
        "cc": cc,
        "messages": messages,
        "downloads": [{"label": label, **_file_entry(path)} for label, path in payload.downloads],
        "notes": [*payload.notes, *payload.sender_notes],
        "detail": payload.detail,
    }
