"""Untagged Invoices Report Generator tool routes.

  1. POST /process           - upload the Aging export (+ an optional "as on"
                                date, defaults to today) and run the 4-sheet
                                report pipeline (see
                                app.services.untagged_invoices.processor) as
                                a background job.
  2. GET  /jobs/{job_id}      - poll job status/progress.
  3. GET  /download/{job_id}  - download the finished workbook.
  4. Two mapping CRUD route groups (locations, accounts-incharge), backed by
     the shared MySQL database (see mapping_store.py / models.py).
"""

from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import SessionLocal, get_db
from app.jobs import cancel_job, get_job, submit_job
from app.models import User
from app.permissions import require_app_access
from app.services.untagged_invoices import mapping_store, processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/untagged-invoices-report", tags=["untagged-invoices-report"],
    dependencies=[Depends(require_app_access("untagged-invoices-report"))],
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ── log adapter (mirrors app.routers.unapplied_receipts._LogQueue) ─────────

class _LogQueue:
    def __init__(self, progress_cb=None, start: float = 0.05, end: float = 0.5):
        self._progress_cb = progress_cb
        self._start = start
        self._end = end
        self._count = 0
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        level, msg = item
        self.messages.append((level, msg))
        if self._progress_cb:
            self._count += 1
            frac = min(self._end, self._start + self._count * 0.03)
            try:
                self._progress_cb(frac, msg)
            except Exception:
                pass


# ── /process job pipeline ───────────────────────────────────────────────────

def _run_process_job(input_path: str, output_path: str, as_on_date_str: Optional[str],
                      progress_cb=None, cancel_event=None) -> dict:
    """Runs in a dedicated app.worker process (not tied to any request) -
    opens its own short-lived DB session since the request that queued the
    job has already returned by the time this runs."""
    log_q = _LogQueue(progress_cb)
    as_on_date = date.fromisoformat(as_on_date_str) if as_on_date_str else date.today()

    if progress_cb:
        progress_cb(0.02, "Loading centralized mappings...")
    db = SessionLocal()
    try:
        location_map, incharge_map = mapping_store.load_all(db)
    finally:
        db.close()

    try:
        if progress_cb:
            progress_cb(0.05, "Reading the Aging export...")
        df = processor.read_ageing_file(input_path, log_q)

        missing_locations = processor.missing_location_mappings(df, location_map)
        df = processor.add_location_column(df, location_map)

        resolved_locations = df[processor.LOCATION_COL].astype(str).str.strip()
        missing_incharges = processor.missing_incharge_mappings(
            sorted(resolved_locations.unique()), incharge_map,
        )

        if progress_cb:
            progress_cb(0.30, "Recomputing ageing buckets...")
        df = processor.recompute_ageing_buckets(df, as_on_date, log_q)

        if progress_cb:
            progress_cb(0.45, "Building pivots...")
        df_below_1k = processor.build_below_1k_pivot(df, incharge_map)
        df_untagged_detail = processor.build_untagged_detail(df)
        df_untagged_summary = processor.build_untagged_summary(df_untagged_detail, incharge_map)

        processor.write_report(
            df, df_below_1k, df_untagged_detail, df_untagged_summary,
            output_path, as_on_date, log_q=log_q, progress_cb=progress_cb,
        )
    finally:
        Path(input_path).unlink(missing_ok=True)

    validation_warnings = []
    if missing_locations:
        validation_warnings.append({"category": "Location Not Mapped", "items": missing_locations})
    if missing_incharges:
        validation_warnings.append({"category": "Accounts Incharge Not Mapped", "items": missing_incharges})
    for warning in validation_warnings:
        log_q.put(("warn", f"{warning['category']}: {len(warning['items'])} unmapped value(s)"))

    filename = f"Untagged_Invoices_Report_As_On_{as_on_date.isoformat()}.xlsx"
    log_q.put(("ok", f"Done - {filename}"))

    return {
        "output_path": str(output_path),
        "download_filename": filename,
        "as_on_date": as_on_date.isoformat(),
        "total_rows": int(len(df)),
        "below_1k_row_count": int(len(df_below_1k)),
        "untagged_detail_row_count": int(len(df_untagged_detail)),
        "untagged_summary_row_count": int(len(df_untagged_summary)),
        "validation_warnings": validation_warnings,
        "log": log_q.messages,
    }


@router.post("/process")
async def submit_process(
    file: UploadFile = File(...),
    as_on_date: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    if as_on_date:
        try:
            date.fromisoformat(as_on_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="as_on_date must be an ISO date (YYYY-MM-DD)")

    input_path = await save_upload(file, SCRATCH_DIR)
    output_path = SCRATCH_DIR / f"{input_path.stem}_Untagged_Invoices_Report.xlsx"

    job_id = submit_job(
        _run_process_job,
        str(input_path),
        str(output_path),
        as_on_date,
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
def download_report(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Job is not finished yet")

    result = job.get("result") or {}
    output_path = Path(result.get("output_path", ""))
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    filename = result.get("download_filename") or "Untagged_Invoices_Report.xlsx"
    return FileResponse(path=str(output_path), filename=filename, media_type=_XLSX_MEDIA_TYPE)


# ── request bodies ───────────────────────────────────────────────────────────

class LocationBody(BaseModel):
    location_name: str
    location: Optional[str] = None


class AccountsInchargeBody(BaseModel):
    location: str
    accounts_incharge: Optional[str] = None


# ── 1. Location Map  (key = location_name) ─────────────────────────────────

@router.get("/mappings/locations")
def list_locations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    location_map, _ = mapping_store.load_all(db)
    return [
        {"location_name": location_name, "location": location}
        for location_name, location in sorted(location_map.items())
    ]


@router.post("/mappings/locations")
def add_location(body: LocationBody, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    location_name = body.location_name.strip()
    if not location_name:
        raise HTTPException(status_code=400, detail="location_name is required")
    location = (body.location or "").strip()
    mapping_store.upsert_location(db, location_name, location)
    return {"ok": True, "location_name": location_name.upper(), "location": location}


@router.put("/mappings/locations/{key}")
def edit_location(key: str, body: LocationBody, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    new_location_name = body.location_name.strip()
    location = (body.location or "").strip()

    if new_location_name.upper() != key.upper():
        if not mapping_store.delete_location(db, key):
            raise HTTPException(status_code=404, detail="Location name not found")
    mapping_store.upsert_location(db, new_location_name, location)
    return {"ok": True, "location_name": new_location_name.upper(), "location": location}


@router.delete("/mappings/locations/{key}")
def delete_location(key: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    if not mapping_store.delete_location(db, key):
        raise HTTPException(status_code=404, detail="Location name not found")
    return {"ok": True}


# ── 2. Accounts Incharge Map  (key = location) ──────────────────────────────

@router.get("/mappings/accounts-incharge")
def list_accounts_incharge(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, incharge_map = mapping_store.load_all(db)
    return [
        {"location": location, "accounts_incharge": incharge}
        for location, incharge in sorted(incharge_map.items())
    ]


@router.post("/mappings/accounts-incharge")
def add_accounts_incharge(body: AccountsInchargeBody, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    location = body.location.strip()
    if not location:
        raise HTTPException(status_code=400, detail="location is required")
    incharge = (body.accounts_incharge or "").strip()
    mapping_store.upsert_accounts_incharge(db, location, incharge)
    return {"ok": True, "location": location, "accounts_incharge": incharge}


@router.put("/mappings/accounts-incharge/{key}")
def edit_accounts_incharge(key: str, body: AccountsInchargeBody, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    new_location = body.location.strip()
    incharge = (body.accounts_incharge or "").strip()

    if new_location != key:
        if not mapping_store.delete_accounts_incharge(db, key):
            raise HTTPException(status_code=404, detail="Location not found")
    mapping_store.upsert_accounts_incharge(db, new_location, incharge)
    return {"ok": True, "location": new_location, "accounts_incharge": incharge}


@router.delete("/mappings/accounts-incharge/{key}")
def delete_accounts_incharge(key: str, db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    if not mapping_store.delete_accounts_incharge(db, key):
        raise HTTPException(status_code=404, detail="Location not found")
    return {"ok": True}
