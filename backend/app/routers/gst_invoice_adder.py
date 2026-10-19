"""GST Invoice Number Adder tool routes.

Ports the desktop app "RDCTRAK GST Invoice Number Enricher v12". The open
receivables ageing is fetched live from the ERP (app.services.erp_ageing) and
enriched with a new "GST Invoice Number" column, looked up from Oracle per
(invoice no, invoice date) pair. See services/gst_invoice_adder/processor.py
for the ported business logic and why it must not be "simplified".

Nothing is uploaded: the ageing is always the latest, as on today.

No mapping tables and no email feature in the original desktop app, so
neither is added here - this is a pure background job -> download tool.

Oracle connectivity reuses the SAME ORACLE_* env vars (same actual Oracle
server) already wired up for services/unapplied_receipts - see app.config.
"""

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
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
from app.jobs import cancel_job, run_cpu_phase, submit_job, get_job
from app.permissions import require_app_access
from app.services import erp_ageing
from app.services.gst_invoice_adder import processor

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
    itself is reported separately, via _job_enrich's own progress_cb calls
    at meaningful checkpoints - mirrors app.routers.unaccounted_txn's
    _LogQueue in spirit, simplified since this tool has explicit progress
    checkpoints rather than needing to synthesize them from log volume."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def _cpu_phase_write_output(output_path: str, df, gst_map: dict):
    """Step 3 - 100% CPU (openpyxl write), no Oracle/DB involved, so it runs on
    the CPU process pool. Uses a plain picklable log_q (not the router's real
    _LogQueue, which isn't picklable) - its messages are merged into the outer
    log after this call returns."""
    log_q = _CollectingLogQueue()
    total, found, blank = processor.build_gst_report_pure_python(
        df, processor.COL_INV_NO, processor.COL_INV_DATE, gst_map, output_path, log_q,
    )
    return total, found, blank, log_q.messages


class _CollectingLogQueue:
    """Plain picklable log_q for use inside a CPU-phase subprocess call -
    see _cpu_phase_write_output."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def _job_enrich(output_path: str, download_name: str,
                 progress_cb=None, cancel_event=None) -> dict:
    """Fetch the ageing from the ERP, look up each (invoice no, date) pair's
    GST number, and write the enriched workbook. The Oracle fetches stay on
    this thread (I/O-bound, already their own ThreadPoolExecutor + connection
    pool); the CPU-bound write runs on the CPU process pool."""
    log_q = _LogQueue()

    if progress_cb:
        progress_cb(0.02, "Fetching the ageing from the ERP...")
    # the fetch reports 0.05-0.25; squeeze that into this job's 0.02-0.09
    fetch_progress = (
        (lambda frac, msg: progress_cb(0.02 + (frac - 0.05) / 0.2 * 0.07, msg)) if progress_cb else None
    )
    df = erp_ageing.fetch_ageing_from_erp(
        _ORACLE_CFG, log_q, progress_cb=fetch_progress, cancel_event=cancel_event,
    )
    pairs_unique = processor._extract_pairs_vectorized(df, processor.COL_INV_NO, processor.COL_INV_DATE)
    log_q.put(("info", f"{len(df)} data rows found"))
    log_q.put(("info", f"{len(pairs_unique)} unique (invoice no, date) pairs"))

    if progress_cb:
        progress_cb(0.09, "Connecting to Oracle...")
    processor.init_oracle_client(_ORACLE_CFG.instant_client_dir)
    pool = processor._make_pool(_ORACLE_CFG, log_q)
    try:
        gst_map = processor._fetch_all_parallel(
            pairs_unique,
            pool,
            log_q,
            progress_cb,
            cancel_event=cancel_event,
        )
    finally:
        try:
            pool.close(force=bool(cancel_event is not None and cancel_event.is_set()))
        except TypeError:
            pool.close()
        except Exception:
            pass

    if progress_cb:
        progress_cb(0.90, "Preparing output workbook...")
    ok_cnt = sum(1 for v in gst_map.values() if v)
    log_q.put(("info", f"{ok_cnt}/{len(gst_map)} keys returned a GST number"))

    if progress_cb:
        progress_cb(0.92, "Writing enriched output...")
    total, found, blank, cpu_log_messages = run_cpu_phase(
        _cpu_phase_write_output, output_path, df, gst_map,
    )
    log_q.messages.extend(cpu_log_messages)

    if progress_cb:
        progress_cb(1.0, "Done")

    return {
        "total": total,
        "found": found,
        "blank": blank,
        "output_path": output_path,
        "download_name": download_name,
        "log": log_q.messages,
    }


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
