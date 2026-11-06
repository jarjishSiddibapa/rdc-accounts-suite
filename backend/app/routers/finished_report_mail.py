"""Email a finished report by hand, from any report application.

``make_finished_report_mail_router`` is included into the router of an application whose job leaves one
workbook behind (``result["output_path"]``, optionally ``download_filename``): after a report has been
generated, the person can email it instead of only downloading it. Open to everyone with that application,
exactly like its Download button:

  GET  /email/defaults - To / Cc to start from (the last mail's, else the admin defaults), subject and text.
  POST /email/send     - attach the finished report of the person's own job and send it from their own
                         email identity (Settings). Recorded in the application's send history.
  GET  /email/log      - the application's send history.
"""

import html
import re
import shutil
import smtplib
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import get_db
from app.jobs import claim_job_action, finish_job_action, get_job
from app.rate_limit import auth_limiter
from app.regional import now_ist
from app.services import mailer_shared
from app.services import scheduled_report_mail as mail
from app.validation import normalize_email

_NOT_CONFIGURED = "You haven’t set up your email sender yet. Go to Settings."
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class SendBody(BaseModel):
    job_id: str
    request_id: str                      # one per click on "Send", so a double click cannot send twice
    to: list[str] = Field(min_length=1, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(min_length=1, max_length=300)
    message: str = Field(min_length=1, max_length=5000)


def _emails(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        if str(value).strip():
            email = normalize_email(str(value))
            if email not in cleaned:
                cleaned.append(email)
    return cleaned


def attachment_name(result: dict, output_path: Path, label: str) -> str:
    """The name the recipient sees: the one the Download button gives, else the original file's, else the label."""
    raw = result.get("download_filename") or result.get("original_filename") or label
    name = _UNSAFE_NAME.sub(" ", Path(str(raw)).name).strip(" .") or label
    suffix = output_path.suffix or ".xlsx"
    return name if name.lower().endswith(suffix.lower()) else f"{name}{suffix}"


def message_html(message: str, signature: str = "") -> str:
    body = html.escape(message.strip()).replace("\r\n", "\n").replace("\n", "<br>\n")
    html_body = f'<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;">{body}</div>'
    if signature and signature.strip():
        escaped = html.escape(signature.strip()).replace("\r\n", "\n").replace("\n", "<br>")
        html_body += f'<p style="margin-top:24px;font-size:13px;line-height:1.6;">{escaped}</p>'
    return html_body


def make_finished_report_mail_router(app_key: str, label: str) -> APIRouter:
    router = APIRouter(prefix="/email")

    @router.get("/defaults")
    def get_defaults(db: Session = Depends(get_db), user=Depends(get_current_user)):
        recipients = mailer_shared.get_prefill_recipients(db, app_key)
        sender = mailer_shared.get_email_settings(user.id)
        return {
            "to": recipients["to"],
            "cc": recipients["cc"],
            "subject": f"{label} as on {now_ist():%d-%b-%Y}",
            "message": f"Dear Team,\n\nPlease find attached the {label}.",
            "sender_configured": sender["configured"],
            "sender_email": sender["email"],
        }

    @router.post("/send")
    def send(body: SendBody, user=Depends(get_current_user)):
        auth_limiter.enforce(f"email-report:{app_key}:{user.id}", limit=30, window_seconds=10 * 60)
        sender = mailer_shared.get_email_settings(user.id)
        if not sender["configured"]:
            raise HTTPException(status_code=400, detail=_NOT_CONFIGURED)
        if not _REQUEST_ID.match(body.request_id):
            raise HTTPException(status_code=400, detail="Invalid request.")
        try:
            to_list, cc_list = _emails(body.to), _emails(body.cc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        subject, message = body.subject.strip(), body.message.strip()
        if not to_list:
            raise HTTPException(status_code=400, detail="Add at least one To recipient.")
        if set(to_list) & set(cc_list):
            raise HTTPException(status_code=400, detail="An address can be in To or Cc, not both.")
        if not subject or "\n" in subject or "\r" in subject:
            raise HTTPException(status_code=400, detail="The subject must be a single, non-empty line.")
        if not message:
            raise HTTPException(status_code=400, detail="The mail text cannot be empty.")

        job = get_job(body.job_id, owner_id=user.id)
        if job is None:
            raise HTTPException(status_code=404, detail="Report not found.")
        if job.get("status") != "done":
            raise HTTPException(status_code=409, detail="The report is not finished yet.")
        result = job.get("result") or {}
        output_path = Path(result.get("output_path", "")) if isinstance(result, dict) else Path("")
        if not output_path.is_file():
            raise HTTPException(status_code=404, detail="The report file is no longer available. Generate it again.")
        size = output_path.stat().st_size
        if size > mail.MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"The report is {size / 1024 / 1024:.1f} MB, too large to email. Download it and share it another way.",
            )

        claim_state, _ = claim_job_action(body.job_id, owner_id=user.id, action=f"email-{body.request_id}")
        if claim_state != "claimed":
            detail = {
                "in_progress": "This email is already being sent.",
                "completed": "This email has already been sent.",
                "failed": "That attempt did not complete. Press Send again.",
            }.get(claim_state, "Report not found.")
            raise HTTPException(status_code=404 if claim_state == "missing" else 409, detail=detail)

        started = datetime.now(UTC).replace(tzinfo=None)
        name = attachment_name(result, output_path, label)
        recipients = {"to": to_list, "cc": cc_list}
        detail_log = {"job_id": body.job_id, "sent_from": sender["email"], "attachment": name, "bytes": size}
        staging = Path(tempfile.mkdtemp(prefix="email-report-", dir=SCRATCH_DIR))
        try:
            attachment = staging / name          # the recipient sees the friendly name, not the scratch file's
            shutil.copyfile(output_path, attachment)
            mailer_shared.send_mail(
                from_email=sender["email"],
                app_password=sender["app_password"],
                to_addresses=to_list,
                cc_addresses=cc_list,
                subject=subject,
                html_body=message_html(message, sender.get("signature", "")),
                attachments=[str(attachment)],
            )
        except Exception as exc:  # noqa: BLE001 - reported to the person, logged, then re-raised as HTTP
            finish_job_action(body.job_id, owner_id=user.id, action=f"email-{body.request_id}", succeeded=False)
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                problem = "Gmail rejected your sender email or app password. Check them in Settings."
            else:
                problem = f"The mail could not be sent: {str(exc)[:300]}"
            mail.record_manual_send(app_key, user.email, started, "failed", problem, recipients, [], detail_log)
            raise HTTPException(status_code=502, detail=problem) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        finish_job_action(body.job_id, owner_id=user.id, action=f"email-{body.request_id}", succeeded=True)
        sent = [{"subject": subject, "sent_at": datetime.now(UTC).isoformat(), "attachments": [{"name": name, "bytes": size}]}]
        mail.record_manual_send(app_key, user.email, started, "sent", None, recipients, sent, detail_log)
        mailer_shared.remember_recipients(app_key, to_list, cc_list)
        return {"status": "sent", "to": to_list, "cc": cc_list}

    @router.get("/log")
    def get_log(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db), user=Depends(get_current_user)):
        return mail.log_view(db, app_key, limit=limit, offset=offset)

    return router
