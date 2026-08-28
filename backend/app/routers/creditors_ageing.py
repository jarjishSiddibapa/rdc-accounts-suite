"""Ultrafine Creditors Ageing report and centralized mapping routes."""

from datetime import date
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import SessionLocal, get_db
from app.jobs import JobUserError, cancel_job, get_job, submit_job
from app.models import User
from app.permissions import require_app_access
from app.services.creditors_ageing import mapping_store, processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/creditors-ageing",
    tags=["creditors-ageing"],
    dependencies=[Depends(require_app_access("creditors-ageing-report"))],
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class MappingBody(BaseModel):
    vendor_name: str
    location: str = ""
    vendor_type: str = ""
    vendor_sub_type: str = ""
    intercompany: bool = False


def _run_process_job(
    input_path: str,
    output_path: str,
    csv_path: str,
    as_on_date: str | None,
    progress_cb=None,
) -> dict:
    """Durable worker entry point; reloads mappings when execution begins."""
    log: list[tuple[str, str]] = []

    def add_log(level: str, message: str) -> None:
        log.append((level, message))

    db = SessionLocal()
    try:
        mapping = mapping_store.load_all(db)
    finally:
        db.close()

    try:
        result = processor.generate_report(
            input_path,
            output_path,
            mapping,
            as_on_date=as_on_date,
            new_vendors_csv_path=csv_path,
            progress_cb=progress_cb,
            log_cb=add_log,
        )
    except processor.AgeingReportError as exc:
        Path(output_path).unlink(missing_ok=True)
        Path(csv_path).unlink(missing_ok=True)
        raise JobUserError(str(exc)) from exc
    except Exception:
        Path(output_path).unlink(missing_ok=True)
        Path(csv_path).unlink(missing_ok=True)
        raise
    finally:
        Path(input_path).unlink(missing_ok=True)

    result["download_filename"] = result.pop("default_name")
    result["log"] = log
    if progress_cb:
        progress_cb(1.0, "Creditors Ageing report ready")
    return result


@router.post("/process")
async def submit_process(
    file: UploadFile = File(...),
    as_on_date: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(status_code=400, detail="Upload a Tally Excel workbook (.xlsx or .xlsm).")
    if as_on_date:
        try:
            date.fromisoformat(as_on_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Choose a valid report as-on date.") from exc

    input_path = await save_upload(file, SCRATCH_DIR)
    token = str(uuid.uuid4())
    output_path = SCRATCH_DIR / f"{token}_creditors_ageing.xlsx"
    csv_path = SCRATCH_DIR / f"{token}_new_vendors.csv"
    try:
        job_id = submit_job(
            _run_process_job,
            str(input_path),
            str(output_path),
            str(csv_path),
            as_on_date or None,
            owner_id=user.id,
        )
    except Exception:
        input_path.unlink(missing_ok=True)
        raise
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


def _finished_result(job_id: str, user: User) -> dict:
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job is not finished yet")
    return job.get("result") or {}


@router.get("/download/{job_id}")
def download_report(job_id: str, user: User = Depends(get_current_user)):
    result = _finished_result(job_id, user)
    output_path = Path(result.get("output_path", ""))
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(
        output_path,
        filename=result.get("download_filename") or "Ultrafine Creditors Ageing.xlsx",
        media_type=_XLSX_MEDIA_TYPE,
    )


@router.get("/download/{job_id}/new-vendors")
def download_new_vendors(job_id: str, user: User = Depends(get_current_user)):
    result = _finished_result(job_id, user)
    csv_path = Path(result.get("new_vendors_csv_path", ""))
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail="No unresolved-vendor file is available")
    stem = Path(result.get("download_filename") or "Ultrafine Creditors Ageing").stem
    return FileResponse(
        csv_path,
        filename=f"{stem}_NEW_VENDORS_TO_CLASSIFY.csv",
        media_type="text/csv",
    )


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_db)):
    return mapping_store.list_rows(db)


@router.get("/mappings/archived")
def list_archived_mappings(db: Session = Depends(get_db)):
    return mapping_store.list_rows(db, archived=True)


def _save_mapping(db: Session, body: MappingBody, original_name: str | None):
    try:
        mapping_store.upsert_mapping(
            db,
            original_name=original_name,
            vendor_name=body.vendor_name,
            location=body.location,
            vendor_type=body.vendor_type,
            vendor_sub_type=body.vendor_sub_type,
            intercompany=body.intercompany,
        )
    except mapping_store.ArchivedMappingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except mapping_store.DuplicateMappingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/mappings")
def create_mapping(body: MappingBody, db: Session = Depends(get_db)):
    return _save_mapping(db, body, None)


@router.put("/mappings/{vendor_name}")
def update_mapping(vendor_name: str, body: MappingBody, db: Session = Depends(get_db)):
    return _save_mapping(db, body, vendor_name)


@router.delete("/mappings/{vendor_name}")
def archive_mapping(vendor_name: str, db: Session = Depends(get_db)):
    if not mapping_store.archive_mapping(db, vendor_name):
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}


@router.post("/mappings/{vendor_name}/restore")
def restore_mapping(vendor_name: str, db: Session = Depends(get_db)):
    if not mapping_store.restore_mapping(db, vendor_name):
        raise HTTPException(status_code=404, detail="Archived mapping not found")
    return {"ok": True}

