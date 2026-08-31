"""ERP HTML/XLS -> Excel conversion tool routes."""

import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

from app import auth, config, jobs
from app.jobs import JobUserError
from app.models import User
from app.permissions import require_app_access
from app.services.erp_converter import converter
from app.services.erp_converter.errors import ConversionError
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/erp-to-excel", tags=["erp-to-excel"],
    dependencies=[Depends(require_app_access("erp-to-excel"))],
)


def _job_convert(
    input_path: str,
    output_path: str,
    original_filename: str,
    progress_cb=None,
):
    """100% CPU work (HTML/xlrd parsing + openpyxl writing), so it runs on
    the CPU process pool for real multi-core throughput instead of a GIL-
    bound thread. convert_file's own progress_cb (which reports
    fine-grained "Parsing report NN%" / "Writing workbook" progress for
    large HTML exports) is relayed across the process boundary via
    run_cpu_phase's progress queue, so large-file conversions show real,
    moving progress instead of appearing frozen for the whole call.
    Cancellation still only takes effect once this file's conversion
    finishes rather than mid-parse - the underlying CPU work itself isn't
    checkpointed."""
    if progress_cb:
        progress_cb(0.0, "Starting conversion...")
    try:
        try:
            kind = jobs.run_cpu_phase(
                converter.convert_file, input_path, output_path, progress_cb=progress_cb,
            )
        except ConversionError as exc:
            raise JobUserError(str(exc)) from exc
        return {
            "kind": kind,
            "output_path": output_path,
            "original_filename": original_filename,
        }
    finally:
        Path(input_path).unlink(missing_ok=True)


class DownloadAllBody(BaseModel):
    job_ids: list[str]


@router.post("/convert", dependencies=[Depends(auth.get_current_user)])
async def convert(
    files: list[UploadFile] = File(...),
    user: User = Depends(auth.get_current_user),
):
    job_entries = []

    for upload in files:
        original_name = upload.filename or "upload"
        stem = Path(original_name).stem
        input_path = await save_upload(upload, config.SCRATCH_DIR)
        output_path = config.SCRATCH_DIR / f"{input_path.stem}_{stem}.xlsx"

        job_id = jobs.submit_job(
            _job_convert,
            str(input_path),
            str(output_path),
            original_name,
            owner_id=user.id,
        )
        job_entries.append({"filename": original_name, "job_id": job_id})

    return {"jobs": job_entries}


@router.get("/jobs/{job_id}", dependencies=[Depends(auth.get_current_user)])
def job_status(job_id: str, user: User = Depends(auth.get_current_user)):
    job = jobs.get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(auth.get_current_user)])
def cancel(job_id: str, user: User = Depends(auth.get_current_user)):
    job = jobs.cancel_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/download/{job_id}", dependencies=[Depends(auth.get_current_user)])
def download(job_id: str, user: User = Depends(auth.get_current_user)):
    job = jobs.get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job is not finished yet")

    info = job.get("result") or {}
    if not isinstance(info, dict):
        raise HTTPException(status_code=404, detail="Output file not found")

    output_path = Path(info.get("output_path", ""))
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    original_stem = Path(info.get("original_filename") or output_path.stem).stem
    return FileResponse(
        path=str(output_path),
        filename=f"{original_stem}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/download-all", dependencies=[Depends(auth.get_current_user)])
def download_all(body: DownloadAllBody, user: User = Depends(auth.get_current_user)):
    """Bundle every selected completed conversion into one browser download."""
    if not body.job_ids:
        raise HTTPException(status_code=400, detail="No conversion jobs selected")
    if len(body.job_ids) > 100:
        raise HTTPException(status_code=400, detail="At most 100 jobs can be downloaded together")

    # Zips of up to 100 already-converted workbooks: written straight to a
    # scratch file rather than an in-memory BytesIO, so bundling many large
    # conversions together can't hold the whole combined archive (and then
    # a second full copy via .getvalue()) resident in RAM at once.
    zip_path = config.SCRATCH_DIR / f"{uuid.uuid4()}_ERP_Excel_Conversions.zip"
    try:
        used_names: set[str] = set()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for job_id in body.job_ids:
                job = jobs.get_job(job_id, owner_id=user.id)
                if job is None:
                    raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
                if job["status"] != "done":
                    raise HTTPException(status_code=409, detail="Every selected job must be finished")
                info = job.get("result") or {}
                if not isinstance(info, dict):
                    raise HTTPException(status_code=404, detail="Output file not found")
                path = Path(info.get("output_path", ""))
                if not path.is_file():
                    raise HTTPException(status_code=404, detail="Output file not found")

                base_name = f"{Path(info.get('original_filename') or path.stem).stem}.xlsx"
                name = base_name
                suffix = 2
                while name.lower() in used_names:
                    name = f"{Path(base_name).stem}_{suffix}.xlsx"
                    suffix += 1
                used_names.add(name.lower())
                archive.write(path, arcname=name)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise

    return FileResponse(
        path=str(zip_path),
        filename="ERP_Excel_Conversions.zip",
        media_type="application/zip",
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )
