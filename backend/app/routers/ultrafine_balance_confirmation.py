"""Ultrafine Balance Confirmation Bulk Sender routes.

Ports the desktop app (see
E:\\jarjish-projects\\vishal-sir-balance-confirmation-for-ultrafine) — one
balance-confirmation email PER customer row, built from a single uploaded
workbook (Customer Name, Net O/s, optionally To/CC Email IDs) plus an
optional set of per-customer PDF attachments matched by filename.

Mirrors this suite's established two-step "preview then confirm-send" mail
pattern from app/routers/unaccounted_txn.py: nothing is ever sent without a
human reviewing the exact built subject/body/to/cc/attachment first.

Unlike unaccounted_txn's single combined email, this is a BULK per-customer
sender — potentially dozens of individual emails. Both building the preview
and actually sending are run as background jobs (submit_job / get_job from
app/jobs.py) that the frontend polls via ProgressPanel, so neither step is a
long blocking POST that could hit the client's fixed request timeout while
the backend keeps working.
"""

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR, SEED_DIR
from app.database import get_db
from app.jobs import (
    JobUserError,
    cancel_job,
    claim_job_action,
    finish_job_action,
    get_job,
    run_cpu_phase,
    submit_job,
)
from app.permissions import require_app_access
from app.services import mailer_shared
from app.services.ultrafine_balance_confirmation import mapping_store, processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/ultrafine-balance-confirmation",
    tags=["ultrafine-balance-confirmation"],
    dependencies=[Depends(require_app_access("ultrafine-balance-confirmation"))],
)


async def _save_upload(upload: UploadFile, destination: Path = SCRATCH_DIR) -> Path:
    return await save_upload(upload, destination)


_TEMPLATE_PATH = SEED_DIR / "ultrafine_balance_confirmation_template.xlsx"


@router.get("/template")
def download_template():
    """The original desktop app's Balance_Confirmation_Input_Template.xlsx,
    bundled as-is so users can download the required column layout (Customer
    Name, Net O/s, To Email IDs, CC Email IDs) before filling it in."""
    if not _TEMPLATE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Template file not found")
    return FileResponse(
        path=str(_TEMPLATE_PATH),
        filename="Balance_Confirmation_Input_Template.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _cpu_phase_preview(workbook_path: str, pdf_pairs: list, as_on_date: str, signature: str, mapping: dict):
    """100% CPU (pandas read + plan building), no I/O - runs on the CPU
    process pool. build_send_plan's own progress_cb (fine-grained "Built
    preview N of M") can't cross the process boundary, so the UI shows one
    "Building preview..." phase for the whole call instead of a live count -
    a cosmetic regression, not a correctness one, and a minor one in
    practice since building a plan for even a few hundred customers is fast."""
    df = processor.read_input_excel(workbook_path)
    rows = processor.rows_from_input(df)
    pdf_lookup = processor.build_pdf_lookup(pdf_pairs)
    return processor.build_send_plan(rows, mapping, signature, as_on_date or None, pdf_lookup)


# ── Job bodies (run in the background thread pool) ────────────────────────────

def _job_preview(
    workbook_path: str,
    pdf_pairs: list,
    as_on_date: str,
    signature: str,
    mapping: dict,
    from_email: str,
    progress_cb=None,
) -> dict:
    try:
        if progress_cb:
            progress_cb(0.05, "Building preview...")
        plan = run_cpu_phase(_cpu_phase_preview, workbook_path, pdf_pairs, as_on_date, signature, mapping)
        if progress_cb:
            progress_cb(0.95, "Preview ready")
    finally:
        # Only the workbook is done with after this job - the matched PDFs
        # (pdf_pairs) are referenced by path inside `plan` and still needed
        # by _job_send once the user confirms, so they're cleaned up there.
        Path(workbook_path).unlink(missing_ok=True)

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


def _job_send(user_id: int, customers: list, progress_cb=None) -> dict:
    settings = mailer_shared.get_email_settings(user_id)
    if not settings.get("configured"):
        raise JobUserError("Your email sender is no longer configured. Update Settings and try again.")
    try:
        report = processor.send_bulk_mails(
            settings["email"], settings["app_password"], customers, progress_cb=progress_cb
        )
        return {
            "status": "sent",
            "report": report,
            "sent": sum(1 for r in report if r["status"] == "sent"),
            "failed": sum(1 for r in report if r["status"] == "failed"),
            "skipped": sum(1 for r in report if r["status"] == "skipped"),
        }
    finally:
        for customer in customers:
            pdf_path = customer.get("pdf_attachment_path")
            if pdf_path:
                Path(pdf_path).unlink(missing_ok=True)


# ── Preview / confirm-send ──────────────────────────────────────────────────

@router.post("/preview")
async def preview(
    workbook: UploadFile = File(...),
    pdf_files: list[UploadFile] = File(default=[]),
    as_on_date: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Step 1 of 2: build the full per-customer send plan (recipients merged
    with the saved Customer -> Email mapping, matching PDF, and the exact
    subject/body mail_builder would produce) as a background job. Never
    sends anything — the frontend must show this to the user and get an
    explicit confirm before POST /confirm-send is ever called."""
    settings = mailer_shared.get_email_settings(user.id)
    if not settings.get("configured"):
        raise HTTPException(
            status_code=400,
            detail="You haven't set up your email sender yet — go to Settings.",
        )

    # Grouped into one shared "balance-confirm-<uuid>" directory (rather than
    # loose SCRATCH_DIR files) so the scratch-cleanup sweep can give this
    # preview-then-confirm-send workflow its own generous grace period
    # instead of the shorter general-purpose scratch_cleanup_minutes - see
    # app/scheduler.py's _sweep_scratch. Reaping this too early is exactly
    # what silently corrupted an Unaccounted Transactions email once.
    job_dir = SCRATCH_DIR / f"balance-confirm-{uuid.uuid4()}"
    job_dir.mkdir(parents=True, exist_ok=True)

    workbook_path = str(await _save_upload(workbook, job_dir))

    pdf_pairs = []
    for pdf in pdf_files:
        saved_path = str(await _save_upload(pdf, job_dir))
        pdf_pairs.append((pdf.filename or "", saved_path))

    mapping = mapping_store.load_all(db)

    job_id = submit_job(
        _job_preview,
        workbook_path,
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
    workbook) and hands it to SMTP as its OWN background job, so the client
    doesn't have to hold a single long-lived POST open while dozens of
    emails go out."""
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

    claim_state, _ = claim_job_action(
        body.job_id,
        owner_id=user.id,
        action="confirm-send",
    )
    if claim_state != "claimed":
        detail = {
            "in_progress": "These emails are already being sent from another tab.",
            "completed": "This preview has already been sent.",
            "failed": (
                "The previous send attempt did not complete safely. Generate a fresh "
                "preview before trying again to avoid duplicate email."
            ),
        }.get(claim_state, "Job not found.")
        raise HTTPException(status_code=404 if claim_state == "missing" else 409, detail=detail)

    try:
        send_job_id = submit_job(
            _job_send,
            user.id,
            result["customers"],
            owner_id=user.id,
        )
    except Exception:
        finish_job_action(
            body.job_id,
            owner_id=user.id,
            action="confirm-send",
            succeeded=False,
        )
        raise
    finish_job_action(
        body.job_id,
        owner_id=user.id,
        action="confirm-send",
        succeeded=True,
    )
    return {"job_id": send_job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user=Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user=Depends(get_current_user)):
    job = cancel_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Customer -> Email mapping CRUD (independent of sending) ─────────────────

class MappingBody(BaseModel):
    customer_name: str
    to_emails: str = ""
    cc_emails: str = ""


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_db)):
    mapping = mapping_store.load_all(db)
    return [
        {
            "customer_name": name,
            "to_emails": ", ".join(data.get("to", [])),
            "cc_emails": ", ".join(data.get("cc", [])),
        }
        for name, data in sorted(mapping.items())
    ]


@router.post("/mappings")
def create_mapping(body: MappingBody, db: Session = Depends(get_db)):
    customer_name = body.customer_name.strip()
    if not customer_name:
        raise HTTPException(status_code=400, detail="customer_name is required")
    mapping_store.upsert_customer_mapping(
        db, customer_name, processor.split_emails(body.to_emails), processor.split_emails(body.cc_emails),
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
        db, new_name, processor.split_emails(body.to_emails), processor.split_emails(body.cc_emails),
    )
    return {"ok": True}


@router.delete("/mappings/{customer_name}")
def delete_mapping(customer_name: str, db: Session = Depends(get_db)):
    if not mapping_store.delete_customer_mapping(db, customer_name):
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}
