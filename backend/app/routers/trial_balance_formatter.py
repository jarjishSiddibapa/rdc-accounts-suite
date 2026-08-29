"""Ultrafine Trial Balance Formatter report and ledger-nature mapping routes."""

from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import SessionLocal, get_db
from app.jobs import JobUserError, cancel_job, get_job, submit_job
from app.models import User
from app.permissions import require_app_access
from app.services.trial_balance_formatter import mapping_store, processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/trial-balance-formatter",
    tags=["trial-balance-formatter"],
    dependencies=[Depends(require_app_access("trial-balance-formatter"))],
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class NatureBody(BaseModel):
    ledger_name: str
    nature: str
    is_subgroup: bool = False


def _run_process_job(input_path: str, output_path: str, progress_cb=None) -> dict:
    """Durable worker entry point; reloads the nature mapping when execution begins."""
    log: list[tuple[str, str]] = []

    def add_log(level: str, message: str) -> None:
        log.append((level, message))

    db = SessionLocal()
    try:
        nature_map = mapping_store.load_all(db)
    finally:
        db.close()

    try:
        result = processor.generate_report(
            input_path,
            output_path,
            nature_map,
            progress_cb=progress_cb,
            log_cb=add_log,
        )
    except processor.TrialBalanceReportError as exc:
        Path(output_path).unlink(missing_ok=True)
        raise JobUserError(str(exc)) from exc
    except Exception:
        Path(output_path).unlink(missing_ok=True)
        raise
    finally:
        Path(input_path).unlink(missing_ok=True)

    result["log"] = log
    return result


@router.post("/process")
async def submit_process(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise HTTPException(status_code=400, detail="Upload the raw Tally trial balance export (.xlsx or .xlsm).")

    input_path = await save_upload(file, SCRATCH_DIR)
    token = str(uuid.uuid4())
    output_path = SCRATCH_DIR / f"{token}_trial_balance_formatter.xlsx"
    try:
        job_id = submit_job(
            _run_process_job,
            str(input_path),
            str(output_path),
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


@router.get("/download/{job_id}")
def download_report(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job is not finished yet")
    result = job.get("result") or {}
    output_path = Path(result.get("output_path", ""))
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(
        output_path,
        filename=result.get("download_filename") or "Ultrafine Trial Balance.xlsx",
        media_type=_XLSX_MEDIA_TYPE,
    )


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_db)):
    return mapping_store.list_rows(db)


@router.get("/mappings/archived")
def list_archived_mappings(db: Session = Depends(get_db)):
    return mapping_store.list_rows(db, archived=True)


def _save_nature(db: Session, body: NatureBody, original_name: str | None = None):
    try:
        mapping_store.set_nature(
            db,
            body.ledger_name,
            body.nature,
            body.is_subgroup,
            original_name=original_name,
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
def create_mapping(body: NatureBody, db: Session = Depends(get_db)):
    return _save_nature(db, body)


@router.put("/mappings/{ledger_name}")
def update_mapping(ledger_name: str, body: NatureBody, db: Session = Depends(get_db)):
    return _save_nature(db, body, ledger_name)


@router.delete("/mappings/{ledger_name}")
def archive_mapping(ledger_name: str, db: Session = Depends(get_db)):
    if not mapping_store.archive_nature(db, ledger_name):
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"ok": True}


@router.post("/mappings/{ledger_name}/restore")
def restore_mapping(ledger_name: str, db: Session = Depends(get_db)):
    if not mapping_store.restore_nature(db, ledger_name):
        raise HTTPException(status_code=404, detail="Archived mapping not found")
    return {"ok": True}
