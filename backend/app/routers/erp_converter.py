"""ERP HTML/XLS -> Excel conversion tool routes."""

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app import auth, config, jobs
from app.models import User
from app.permissions import require_app_access
from app.services.erp_converter import converter
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
    bound thread. Trade-off: convert_file's own progress_cb (which reports
    fine-grained "Parsing report NN%" progress for large HTML exports) can't
    cross the process boundary, so the UI shows a single "Converting..."
    phase for the whole call instead of a live percentage - a cosmetic
    regression for very large files, not a correctness one; cancellation
    still works, just only takes effect once this file's conversion
    finishes rather than mid-parse."""
    if progress_cb:
        progress_cb(0.02, "Converting...")
    try:
        kind = jobs.run_cpu_phase(converter.convert_file, input_path, output_path)
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

    output = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
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

    return Response(
        content=output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ERP_Excel_Conversions.zip"'},
    )
