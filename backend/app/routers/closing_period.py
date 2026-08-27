"""Closing Period Report Generator tool routes.

Ports the desktop app's flow onto the shared FastAPI backend:

  1. POST /combine           - upload several Oracle BI Publisher HTML-in-.xls
                                closing-period inventory reports, run the
                                parse -> filter -> combine -> write pipeline
                                (app/services/closing_period/combiner.py) as
                                a background job.
  2. GET  /jobs/{job_id}      - poll job status/progress.
  3. GET  /download/{job_id}  - download the finished combined workbook.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.jobs import cancel_job, get_job, submit_job
from app.models import User
from app.permissions import require_app_access
from app.services.closing_period import combiner
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/closing-period", tags=["closing-period"],
    dependencies=[Depends(require_app_access("closing-period-report"))],
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ── log adapter (mirrors app/routers/gstr2b.py's _LogQueue: adapts
#    combiner.run_combine's log_q.put((tag, msg)) calls to the background
#    job's optional progress_cb(frac, phase) callback, while keeping every
#    message for display) ──────────────────────────────────────────────────

class _LogQueue:
    def __init__(self, progress_cb=None, start=0.05, end=0.9):
        self._progress_cb = progress_cb
        self._start = start
        self._end = end
        self._count = 0
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        tag, msg = item
        self.messages.append((tag, msg))
        if self._progress_cb:
            self._count += 1
            frac = min(self._end, self._start + self._count * 0.02)
            try:
                self._progress_cb(frac, msg)
            except Exception:
                pass


# ── /combine job pipeline ──────────────────────────────────────────────────

def _run_combine_job(file_pairs: list[tuple[str, str]], output_path: str, progress_cb=None) -> dict:
    """Runs in the background job pool (a plain thread, not tied to any
    request). file_pairs is (original_filename, saved_path) - see
    combiner.run_combine's docstring for why the original name has to
    survive to here."""
    log_q = _LogQueue(progress_cb)
    try:
        result = combiner.run_combine(file_pairs, output_path, log_q)
    finally:
        for _, path in file_pairs:
            Path(path).unlink(missing_ok=True)

    if progress_cb:
        progress_cb(1.0, "Combined workbook ready")

    result["log"] = log_q.messages
    safe_date = str(result.get("date_label", "")).replace("-", "_") or "report"
    result["download_filename"] = f"Closing_Period_Report_{safe_date}.xlsx"
    return result


@router.post("/combine")
async def submit_combine(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded")

    file_pairs: list[tuple[str, str]] = []
    for upload in files:
        input_path = await save_upload(upload, SCRATCH_DIR)
        file_pairs.append((upload.filename or "", str(input_path)))

    # Collision-safe output name: derive from the first saved upload's own
    # uuid4 prefix (already unique per save_upload).
    first_saved = file_pairs[0][1]
    output_path = Path(first_saved).with_name(f"{Path(first_saved).stem}_combined.xlsx")

    job_id = submit_job(
        _run_combine_job,
        file_pairs,
        str(output_path),
        owner_id=user.id,
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user: User = Depends(get_current_user)):
    job = cancel_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/download/{job_id}")
def download_combined(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job is not finished yet")

    result = job.get("result") or {}
    output_path = Path(result.get("output_path", ""))
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    filename = result.get("download_filename") or "closing_period_report.xlsx"
    return FileResponse(path=str(output_path), filename=filename, media_type=_XLSX_MEDIA_TYPE)
