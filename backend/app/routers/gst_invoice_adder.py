"""GST Invoice Number Adder tool routes.

Ports the desktop app "RDCTRAK GST Invoice Number Enricher v12". The open
receivables ageing is fetched live from the ERP (app.services.erp_ageing) and
enriched with a new "GST Invoice Number" column, looked up from Oracle per
(invoice no, invoice date) pair. See services/gst_invoice_adder/processor.py
for the ported business logic and why it must not be "simplified".

Nothing is uploaded: the ageing is always the latest, as on today.

No mapping tables in the original desktop app, so none are added here. The
report can also be mailed automatically (see auto_mail_routes.py).

Oracle connectivity reuses the SAME ORACLE_* env vars (same actual Oracle
server) already wired up for services/unapplied_receipts - see app.config.
"""

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.jobs import cancel_job, submit_job, get_job
from app.permissions import require_app_access
from app.routers.auto_mail_routes import make_auto_mail_router
from app.routers.report_mail_routes import make_report_mail_router
from app.services.gst_invoice_adder import auto_mail, report_job

router = APIRouter(
    prefix="/api/tools/gst-invoice-adder", tags=["gst-invoice-adder"],
    dependencies=[Depends(require_app_access("gst-invoice-adder"))],
)

router.include_router(make_auto_mail_router("gst-invoice-adder"))
router.include_router(make_report_mail_router("gst-invoice-adder", auto_mail.run_preview_job))

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _job_enrich(output_path: str, download_name: str,
                 progress_cb=None, cancel_event=None) -> dict:
    """Fetch the ageing from the ERP and add each row's GST invoice number."""
    return report_job.build_report(
        output_path, download_name, progress_cb=progress_cb, cancel_event=cancel_event,
    ).result


@router.post("/process")
def process(user=Depends(get_current_user)):
    output_path = SCRATCH_DIR / f"{uuid.uuid4()}_gst_invoice_numbers.xlsx"
    download_name = f"GST_Invoice_Numbers_As_On_{date.today().isoformat()}.xlsx"

    job_id = submit_job(
        _job_enrich, str(output_path), download_name,
        owner_id=user.id,
    )
    return {"job_id": job_id}


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


@router.get("/download/{job_id}")
def download(job_id: str, user=Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job is not finished yet")

    result = job.get("result") or {}
    output_path = Path(result.get("output_path") or "")
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        path=str(output_path),
        filename=result.get("download_name") or output_path.name,
        media_type=_XLSX_MEDIA_TYPE,
    )
