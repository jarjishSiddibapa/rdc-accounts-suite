"""Ultrafine Bulk Payment Reminder Sender routes.

Ports the desktop app (see the payment-reminder ported logic in
app/services/ultrafine_payment_reminder/processing.py) — one payment
reminder / dunning email PER customer (aging) row, built from an uploaded
balance/aging workbook plus an optional Emails workbook and per-customer PDF
attachments matched by filename.

Mirrors this suite's established two-step "preview then confirm-send" mail
pattern used by ultrafine_balance_confirmation.py (this tool's sibling —
same author's architecture pattern applied to a different business need):
nothing is ever sent without a human reviewing the exact built
subject/body/to/cc/attachment first. Both building the preview and actually
sending run as background jobs (submit_job / get_job from app/jobs.py) that
the frontend polls via ProgressPanel.

Unlike the balance-confirmation sibling — whose single input workbook holds
both balances and recipients — this tool takes two separate workbooks (a
required data/aging file and an optional emails file), mirroring the
original desktop app's excel_reader split.
"""

import io
import uuid
from pathlib import Path
from typing import Optional

import openpyxl
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import get_db
from app.jobs import submit_job, get_job
from app.permissions import require_app_access
from app.services import mailer_shared
from app.services.ultrafine_payment_reminder import mapping_store, processing
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/ultrafine-payment-reminder",
    tags=["ultrafine-payment-reminder"],
    dependencies=[Depends(require_app_access("ultrafine-payment-reminder"))],
)


async def _save_upload(upload: UploadFile) -> Path:
    return await save_upload(upload, SCRATCH_DIR)


_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/template")
def download_template():
    """No bundled template shipped with the original desktop app for this
    tool (unlike its Balance Confirmation sibling), so this generates a
    blank workbook with the exact required headers on the fly — Data sheet
    (processing.DATA_REQUIRED_COLUMNS) and Emails sheet
    (processing.EMAILS_REQUIRED_COLUMNS) — so users know the required column
    layout before filling it in."""
    wb = openpyxl.Workbook()
    data_sheet = wb.active
    data_sheet.title = "Data"
    data_sheet.append(processing.DATA_REQUIRED_COLUMNS)
    emails_sheet = wb.create_sheet("Emails")
    emails_sheet.append(processing.EMAILS_REQUIRED_COLUMNS)

    buffer = io.BytesIO()
    wb.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="Payment_Reminder_Input_Template.xlsx"'},
    )


# ── Job bodies (run in the background thread pool) ────────────────────────────

def _job_preview(
    data_path: str,
    emails_path: Optional[str],
    pdf_pairs: list,
    as_on_date: str,
    signature: str,
    mapping: dict,
    from_email: str,
    progress_cb=None,
) -> dict:
    if progress_cb:
        progress_cb(0.02, "Reading balance/aging workbook")
    data_df = processing.read_data_excel(data_path)

    emails_df = None
    if emails_path:
        if progress_cb:
            progress_cb(0.06, "Reading emails workbook")
        emails_df = processing.read_emails_excel(emails_path)

    if progress_cb:
        progress_cb(0.1, "Merging and grouping customers")
    groups = processing.merge_and_group_data(data_df, emails_df, mapping)

    pdf_index = processing.build_pdf_index(pdf_pairs)
    plan = processing.build_send_plan(
        groups, signature, as_on_date or None, pdf_index, progress_cb=progress_cb
    )
    sendable = [c for c in plan if not c["skip_reason"]]
    skipped = [c for c in plan if c["skip_reason"]]
    return {
        "status": "preview",
        "from_email": from_email,
        "customers": plan,
        "total": len(plan),
        "sendable_count": len(sendable),
        "skipped_count": len(skipped),
        "customer_pdfs_found": sum(1 for c in sendable if c["pdf_attached"]),
    }


def _job_send(from_email: str, app_password: str, customers: list, progress_cb=None) -> dict:
    report = processing.send_bulk_mails(from_email, app_password, customers, progress_cb=progress_cb)
    return {
        "status": "sent",
        "report": report,
        "sent": sum(1 for r in report if r["status"] == "sent"),
        "failed": sum(1 for r in report if r["status"] == "failed"),
        "skipped": sum(1 for r in report if r["status"] == "skipped"),
    }


# ── Preview / confirm-send ──────────────────────────────────────────────────

@router.post("/preview")
async def preview(
    data_file: UploadFile = File(...),
    emails_file: Optional[UploadFile] = File(default=None),
    pdf_files: list[UploadFile] = File(default=[]),
    as_on_date: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 1 of 2: build the full per-customer send plan (recipients merged
    with the saved Customer -> Email mapping, matching PDF, and the exact
    subject/body processing.build_subject/build_mail_body would produce) as
    a background job. Never sends anything — the frontend must show this to
    the user and get an explicit confirm before POST /confirm-send is ever
    called."""
    settings = mailer_shared.get_email_settings(user.id)
    if not settings.get("configured"):
        raise HTTPException(
            status_code=400,
            detail="You haven't set up your email sender yet — go to Settings.",
        )

    data_path = str(await _save_upload(data_file))

    emails_path = None
    if emails_file is not None and emails_file.filename:
        emails_path = str(await _save_upload(emails_file))

    pdf_pairs = []
    for pdf in pdf_files:
        saved_path = str(await _save_upload(pdf))
        pdf_pairs.append((pdf.filename or "", saved_path))

    mapping = mapping_store.load_all(db)

    job_id = submit_job(
        _job_preview,
        data_path,
        emails_path,
        pdf_pairs,
        as_on_date,
        settings.get("signature", ""),
        mapping,
        settings["email"],
        owner_id=user.id,
    )
    return {"job_id": job_id}


class ConfirmSendBody(BaseModel):
    job_id: str


@router.post("/confirm-send")
def confirm_send(body: ConfirmSendBody, user=Depends(get_current_user)):
    """Step 2 of 2 — the only endpoint that actually dispatches emails.
    Requires a completed /preview job whose result the user has already
    reviewed; takes the exact already-built per-customer subject/body/
    to/cc/attachment from that job's result (never rebuilds or reparses the
    uploaded workbooks) and hands it to SMTP as its OWN background job, so
    the client doesn't have to hold a single long-lived POST open while
    dozens of emails go out."""
    job = get_job(body.job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    result = job.get("result") or {}
    if job.get("status") != "done" or result.get("status") != "preview":
        raise HTTPException(
            status_code=400,
            detail="This job isn't a ready-to-send preview. Generate the preview again.",
        )

    settings = mailer_shared.get_email_settings(user.id)
    if not settings.get("configured"):
        raise HTTPException(
            status_code=400,
            detail="You haven't set up your email sender yet — go to Settings.",
        )

    send_job_id = submit_job(
        _job_send,
        settings["email"],
        settings["app_password"],
        result["customers"],
        owner_id=user.id,
    )
    return {"job_id": send_job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user=Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Customer -> Email mapping CRUD (independent of sending) ─────────────────

class MappingBody(BaseModel):
    customer_name: str
    to_emails: str = ""
    cc_emails: str = ""


def _normalize_email_field(value: str) -> str:
    """Trim/dedupe a comma-or-semicolon separated address list, storing it
    back as the plain ", "-joined string mapping_store.py's load_all/save_all
    expect (unlike the balance-confirmation sibling's mapping_store, this
    tool's store keeps to_emails/cc_emails as strings, not lists)."""
    return ", ".join(processing.split_emails(value))


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_db)):
    mapping = mapping_store.load_all(db)
    return [
        {
            "customer_name": name,
            "to_emails": data.get("to_emails", ""),
            "cc_emails": data.get("cc_emails", ""),
        }
        for name, data in sorted(mapping.items())
    ]


@router.post("/mappings")
def create_mapping(body: MappingBody, db: Session = Depends(get_db)):
    customer_name = body.customer_name.strip()
    if not customer_name:
        raise HTTPException(status_code=400, detail="customer_name is required")
    mapping_store.upsert_customer_mapping(
        db, customer_name, _normalize_email_field(body.to_emails), _normalize_email_field(body.cc_emails),
    )
    return {"ok": True}


@router.put("/mappings/{customer_name}")
def update_mapping(customer_name: str, body: MappingBody, db: Session = Depends(get_db)):
    new_name = body.customer_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="customer_name is required")
    if new_name != customer_name:
        if not mapping_store.delete_customer_mapping(db, customer_name):
            raise HTTPException(status_code=404, detail="Mapping not found")
    mapping_store.upsert_customer_mapping(
        db, new_name, _normalize_email_field(body.to_emails), _normalize_email_field(body.cc_emails),
    )
    return {"ok": True}


@router.delete("/mappings/{customer_name}")
def delete_mapping(customer_name: str, db: Session = Depends(get_db)):
    if not mapping_store.delete_customer_mapping(db, customer_name):
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}
