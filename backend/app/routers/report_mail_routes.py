"""Mail a report by hand: preview first, send on confirmation.

``make_report_mail_router`` is included into each application's own router
(for example ``/api/tools/untagged-invoices-report/mail/...``), open to anyone
with access to that application - exactly like the Unaccounted / MRN / PO mail:

  GET  /mail/defaults           - recipients (the application's defaults),
                                  subject and text to start from.
  POST /mail/preview            - build the report and the exact mail as a
                                  background job; nothing is sent.
  GET  /mail/download/{job}/{n} - save the full report from that preview.
  POST /mail/confirm-send       - the only call that sends. It sends the
                                  already-built mail of a finished preview,
                                  from the person's own email identity
                                  (Settings), and logs it.
"""

import re
import smtplib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.jobs import claim_job_action, finish_job_action, get_job, submit_job
from app.services import mailer_shared
from app.services import scheduled_report_mail as mail
from app.validation import normalize_email

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_NOT_CONFIGURED = "You haven't set up your email sender yet - go to Settings."


class PreviewBody(BaseModel):
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str
    intro: str
    allow_unmapped: bool = False
    sender: str | None = None   # only where the application lets the person choose: "me", "system" or a person's id
    month: str | None = None    # only for a report built per month (the cashflow)


class ConfirmBody(BaseModel):
    job_id: str


def _emails(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        if str(value).strip():
            email = normalize_email(str(value))
            if email not in cleaned:
                cleaned.append(email)
    return cleaned


def _finished_preview(job_id: str, user) -> dict:
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    result = job.get("result") or {}
    if job.get("status") != "done" or result.get("status") != "preview":
        raise HTTPException(
            status_code=400, detail="This isn't a ready-to-send preview. Preview the email again.",
        )
    return result


def make_report_mail_router(app_key: str, preview_job, per_month: bool = False,
                            sender_choice: bool = False) -> APIRouter:
    """``per_month``: the report is one month's (the request names it, and the
    preview job receives it as ``month=``). ``sender_choice``: the person picks
    whom the mail goes from (their own email, the system email or another
    person's); otherwise it always goes from their own."""
    router = APIRouter(prefix="/mail")
    spec = mail.SPECS[app_key]

    @router.get("/defaults")
    def get_defaults(db: Session = Depends(get_db), user=Depends(get_current_user)):
        settings = mail.get_or_create_settings(db, app_key)
        # To / Cc start from the last mail that was sent, else the admin defaults
        recipients = mailer_shared.get_prefill_recipients(db, app_key)
        sender = mailer_shared.get_email_settings(user.id)
        defaults = {
            "to": recipients["to"],
            "cc": recipients["cc"],
            "subject": settings.subject_template,
            "intro": settings.intro_template,
            "placeholders": list(spec.placeholders),
            "sender_configured": sender["configured"],
            "sender_email": sender["email"],
        }
        if sender_choice:
            options = mail.manual_sender_options(db, user)
            usable = {option["value"] for option in options if option["configured"]}
            defaults["sender_options"] = options
            defaults["sender"] = next((value for value in ("me", "system") if value in usable), "me")
            defaults["sender_configured"] = bool(usable)
        return defaults

    @router.post("/preview")
    def start_preview(body: PreviewBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
        if sender_choice:
            identity = mail.resolve_manual_sender(db, body.sender, user.id)
            if not identity["configured"]:
                raise HTTPException(status_code=400, detail=identity["problem"] or _NOT_CONFIGURED)
        elif not mailer_shared.get_email_settings(user.id)["configured"]:
            raise HTTPException(status_code=400, detail=_NOT_CONFIGURED)
        try:
            to_list, cc_list = _emails(body.to), _emails(body.cc)
            if not to_list:
                raise ValueError("Add at least one To recipient.")
            if set(to_list) & set(cc_list):
                raise ValueError("An address can be in To or Cc, not both.")
            subject, intro = body.subject.strip(), body.intro.strip()
            if not subject or "\n" in subject or "\r" in subject:
                raise ValueError("The subject must be a single, non-empty line.")
            if not intro:
                raise ValueError("The mail text cannot be empty.")
            mail.validate_template(subject, spec.placeholders)
            mail.validate_template(intro, spec.placeholders)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        extra = {}
        if sender_choice:
            extra["sender"] = (body.sender or "me").strip()
        if per_month:
            if not body.month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", body.month):
                raise HTTPException(status_code=400, detail="Month must look like 2026-09.")
            extra["month"] = body.month
        job_id = submit_job(
            preview_job, user.id, to_list, cc_list, subject, intro, body.allow_unmapped,
            owner_id=user.id, **extra,
        )
        return {"job_id": job_id}

    @router.get("/download/{job_id}/{index}")
    def download(job_id: str, index: int, user=Depends(get_current_user)):
        result = _finished_preview(job_id, user)
        downloads = result.get("downloads") or []
        if not 0 <= index < len(downloads):
            raise HTTPException(status_code=404, detail="Report not found.")
        path = Path(downloads[index]["path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="The report is no longer available. Preview the email again.")
        return FileResponse(path=str(path), filename=path.name, media_type=_XLSX)

    @router.post("/confirm-send")
    def confirm_send(body: ConfirmBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
        result = _finished_preview(body.job_id, user)
        if sender_choice:
            # the sender that was previewed (what the person saw as "From") is the one that sends
            sender = mail.resolve_manual_sender(db, result.get("sender"), user.id)
            if not sender["configured"]:
                raise HTTPException(status_code=400, detail=sender["problem"] or _NOT_CONFIGURED)
        else:
            sender = mailer_shared.get_email_settings(user.id)
            if not sender["configured"]:
                raise HTTPException(status_code=400, detail=_NOT_CONFIGURED)

        claim_state, _ = claim_job_action(body.job_id, owner_id=user.id, action="confirm-send")
        if claim_state != "claimed":
            detail = {
                "in_progress": "This email is already being sent from another tab.",
                "completed": "This preview has already been sent.",
                "failed": (
                    "The previous send attempt did not complete safely. Preview the email again "
                    "before trying again to avoid duplicate email."
                ),
            }.get(claim_state, "Job not found.")
            raise HTTPException(status_code=404 if claim_state == "missing" else 409, detail=detail)

        started = datetime.now(UTC).replace(tzinfo=None)
        recipients = {"to": result["to"], "cc": result["cc"]}
        detail_log = {**(result.get("detail") or {}), "sent_from": sender["email"]}
        sent: list[dict] = []
        try:
            for message in result["messages"]:
                paths = [attachment["path"] for attachment in message["attachments"]]
                mailer_shared.send_mail(
                    from_email=sender["email"],
                    app_password=sender["app_password"],
                    to_addresses=result["to"],
                    cc_addresses=result["cc"],
                    subject=message["subject"],
                    html_body=message["html_body"],
                    attachments=paths,
                )
                sent.append({
                    "subject": message["subject"],
                    "sent_at": datetime.now(UTC).isoformat(),
                    "attachments": mail.attachment_info(paths),
                })
        except Exception as exc:  # noqa: BLE001 - reported to the person, logged, then re-raised as HTTP
            finish_job_action(body.job_id, owner_id=user.id, action="confirm-send", succeeded=False)
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                problem = (
                    f"Gmail rejected the email or app password of {sender['email']}. "
                    "Check them under Email administration (the system email) or in that person's Settings."
                    if sender_choice
                    else "Gmail rejected your sender email or app password. Check them in Settings."
                )
            else:
                problem = f"The mail could not be sent: {str(exc)[:300]}"
            if sent:
                problem += f" {len(sent)} of {len(result['messages'])} mails had already gone out."
            mail.record_manual_send(app_key, user.email, started, "failed", problem, recipients, sent, detail_log)
            raise HTTPException(status_code=502, detail=problem) from exc

        finish_job_action(body.job_id, owner_id=user.id, action="confirm-send", succeeded=True)
        mail.record_manual_send(app_key, user.email, started, "sent", None, recipients, sent, detail_log)
        mailer_shared.remember_recipients(app_key, result["to"], result["cc"])
        return {"status": "sent", "to": result["to"], "cc": result["cc"], "messages": sent}

    return router
