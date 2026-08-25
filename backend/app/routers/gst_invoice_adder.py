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
from app.jobs import JobUserError, cancel_job, run_cpu_phase, submit_job, get_job
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
    itself is reported separately, via _job_enrich's own progress_cb calls
    at meaningful checkpoints - mirrors app.routers.unaccounted_txn's
    _LogQueue in spirit, simplified since this tool has explicit progress
    checkpoints rather than needing to synthesize them from log volume."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def _cpu_phase_read_and_extract(input_path: str):
    """Step 1 - 100% CPU (pandas read + vectorized key extraction), no
    Oracle/DB involved, so it runs on the CPU process pool."""
    df, inv_no_col_pd, inv_date_col_pd = processor.read_data_df(input_path)
    if len(df) == 0:
        raise JobUserError("No data rows found after skipping header rows.")
    pairs_unique = processor._extract_pairs_vectorized(df, inv_no_col_pd, inv_date_col_pd)
    return df, inv_no_col_pd, inv_date_col_pd, pairs_unique


def _cpu_phase_write_output(input_path: str, output_path: str, df, inv_no_col_pd, inv_date_col_pd, gst_map: dict):
    """Step 3 - 100% CPU (openpyxl read/write), no Oracle/DB involved, so it
    runs on the CPU process pool. Uses a plain picklable log_q (not the
    router's real _LogQueue, which isn't picklable) - its messages are
    merged into the outer log after this call returns."""
    log_q = _CollectingLogQueue()
    ext = input_path.rsplit(".", 1)[-1].lower()
    if ext == "xlsb":
        total, found, blank = processor.build_gst_report_pure_python(
            df, inv_no_col_pd, inv_date_col_pd, gst_map, output_path, log_q,
        )
    else:
        header_row_1based = processor.SKIP_ROWS + 1
        total, found, blank = processor.insert_gst_column(
            wb_path=input_path, output_path=output_path,
            gst_map=gst_map, header_row=header_row_1based, log_q=log_q,
        )
    return total, found, blank, log_q.messages


class _CollectingLogQueue:
    """Plain picklable log_q for use inside a CPU-phase subprocess call -
    see _cpu_phase_write_output."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def _job_enrich(input_path: str, output_path: str, download_name: str,
                 progress_cb=None) -> dict:
    """Orchestrates process_report's 3 steps directly (rather than calling
    processor.process_report as one opaque call) so the CPU-bound read and
    write steps can each run on the CPU process pool, while the Oracle fetch
    in between - already its own correctly-parallel ThreadPoolExecutor +
    connection pool - stays on this thread exactly as before, since it's
    I/O-bound and gains nothing from a separate process."""
    log_q = _LogQueue()
    try:
        if progress_cb:
            progress_cb(0.02, "Reading input file...")
        df, inv_no_col_pd, inv_date_col_pd, pairs_unique = run_cpu_phase(
            _cpu_phase_read_and_extract, input_path,
        )
        log_q.put(("info", f"{len(df)} data rows found"))
        log_q.put(("info", f"{len(pairs_unique)} unique (invoice no, date) pairs"))

        if progress_cb:
            progress_cb(0.08, "Connecting to Oracle...")
        processor.init_oracle_client(_ORACLE_CFG.instant_client_dir)
        pool = processor._make_pool(_ORACLE_CFG, log_q)
        try:
            gst_map = processor._fetch_all_parallel(pairs_unique, pool, log_q, progress_cb)
        finally:
            try:
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
            _cpu_phase_write_output, input_path, output_path, df, inv_no_col_pd, inv_date_col_pd, gst_map,
        )
        log_q.messages.extend(cpu_log_messages)

        if progress_cb:
            progress_cb(1.0, "Done")
    finally:
        Path(input_path).unlink(missing_ok=True)

    return {
        "total": total,
        "found": found,
        "blank": blank,
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
