"""GST Invoice Number Adder tool routes.

Ports the desktop app "RDCTRAK GST Invoice Number Enricher v12" — uploads an
RDC Receivable Aging Report (.xlsx/.xlsb/.xls) and enriches it with a new
"GST Invoice Number" column, looked up from Oracle per (invoice no, invoice
date) pair. See services/gst_invoice_adder/processor.py for the ported
business logic and why it must not be "simplified".

No mapping tables and no email feature in the original desktop app, so
neither is added here - this is a pure upload -> background job -> download
tool, same shape as trial_balance.py minus the account-picker step.

Oracle connectivity reuses the SAME ORACLE_* env vars (same actual Oracle
server) already wired up for services/unapplied_receipts - see app.config.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.config import (
    ORACLE_HOST,
    ORACLE_INSTANT_CLIENT_DIR,
    ORACLE_PASSWORD,
    ORACLE_PORT,
    ORACLE_SERVICE_NAME,
    ORACLE_USER,
    SCRATCH_DIR,
)
from app.jobs import submit_job, get_job
from app.permissions import require_app_access
from app.services.gst_invoice_adder import processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/gst-invoice-adder", tags=["gst-invoice-adder"],
    dependencies=[Depends(require_app_access("gst-invoice-adder"))],
)

_ORACLE_CFG = processor.OracleConfig(
    host=ORACLE_HOST,
    port=ORACLE_PORT,
    service_name=ORACLE_SERVICE_NAME,
    user=ORACLE_USER,
    password=ORACLE_PASSWORD,
    instant_client_dir=ORACLE_INSTANT_CLIENT_DIR,
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _LogQueue:
    """Adapts processor.py's log_q.put((level, msg)) calls into a plain
    collected list for display in the finished job's result. Progress
    itself is reported separately, via process_report's own progress_cb
    calls at meaningful checkpoints - mirrors app.routers.unaccounted_txn's
    _LogQueue in spirit, simplified since this tool has explicit progress
    checkpoints rather than needing to synthesize them from log volume."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def _job_enrich(input_path: str, output_path: str, download_name: str,
                 progress_cb=None) -> dict:
    log_q = _LogQueue()
    try:
        result = processor.process_report(
            input_path=input_path, output_path=output_path,
            oracle_cfg=_ORACLE_CFG, log_q=log_q, progress_cb=progress_cb,
        )
    finally:
        Path(input_path).unlink(missing_ok=True)

    return {
        **result,
        "output_path": output_path,
        "download_name": download_name,
        "log": log_q.messages,
    }


@router.post("/process")
async def process(file: UploadFile = File(...), user=Depends(get_current_user)):
    input_path = await save_upload(file, SCRATCH_DIR)
    stem = Path(file.filename or "report").stem
    output_path = SCRATCH_DIR / f"{uuid.uuid4()}_{stem}_gst_enriched.xlsx"
    download_name = f"{stem}_gst_enriched.xlsx"

    job_id = submit_job(
        _job_enrich, str(input_path), str(output_path), download_name,
        owner_id=user.id,
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user=Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
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
