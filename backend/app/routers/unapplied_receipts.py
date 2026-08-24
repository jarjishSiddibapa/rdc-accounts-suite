"""RDC Unapplied Receipts Report tool routes.

Ports the desktop app's flow onto the shared FastAPI backend (see
``app.services.unapplied_receipts.processor`` for the full pipeline
docstring):

  1. POST /process          – upload the Unapplied Receipts Register export
                               + the Ageing export (+ an optional "as on"
                               date), run process_report -> classify_advance
                               _customers -> _validate_before_save ->
                               write_formatted_excel as a background job.
  2. GET  /jobs/{job_id}     – poll job status/progress.
  3. GET  /download/{job_id} – download the finished .xlsx once done.
  4. Two CRUD route groups, one per mapping table (accounts-incharge,
     supplier-sites) — both backed by the shared MySQL database (see
     mapping_store.py / models.py), not the desktop app's AppData JSON
     files.

Oracle connectivity: this router is the one place that builds the
``processor.OracleConfig`` value object from ``app.config``'s ``ORACLE_*``
env-driven settings (see config.py's Oracle section) and performs the
one-time ``processor._init_oracle_client()`` call, exactly as
processor.py's own module docstring says it should.
"""

from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

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
from app.database import SessionLocal, get_db
from app.jobs import get_job, submit_job
from app.models import User
from app.permissions import require_app_access
from app.services.unapplied_receipts import mapping_store, processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/unapplied-receipts", tags=["unapplied-receipts"],
    dependencies=[Depends(require_app_access("unapplied-receipts"))],
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Built once from app.config's ORACLE_* env vars (see config.py's Oracle
# section) — every job below reuses this same value object rather than
# re-reading the environment per request.
_ORACLE_CFG = processor.OracleConfig(
    host=ORACLE_HOST,
    port=ORACLE_PORT,
    service_name=ORACLE_SERVICE_NAME,
    user=ORACLE_USER,
    password=ORACLE_PASSWORD,
    instant_client_dir=ORACLE_INSTANT_CLIENT_DIR,
)
# One-time best-effort thick-mode init, exactly as processor.py's module
# docstring says the router should do (init_oracle_client() may only be
# called once per process; processor.py's own query helpers call it again
# defensively before each connection, which is a safe no-op afterwards).
processor._init_oracle_client(_ORACLE_CFG.instant_client_dir)


# ── log adapter (mirrors app.routers.unaccounted_txn._LogQueue) ────────────

class _LogQueue:
    """Adapts processor.py's ``log_q.put((level, msg))`` calls to this
    suite's background-job ``progress_cb(frac, phase)`` callback, while
    keeping every message so it can be returned to the caller for display."""

    def __init__(self, progress_cb=None, start: float = 0.05, end: float = 0.85):
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
            # Nudge the fraction along without knowing the real total steps.
            frac = min(self._end, self._start + self._count * 0.03)
            try:
                self._progress_cb(frac, msg)
            except Exception:
                pass


# ── /process job pipeline ───────────────────────────────────────────────────

def _run_process_job(input_path: str, ageing_path: str, output_path: str,
                      as_on_date_str: Optional[str], progress_cb=None) -> dict:
    """Runs in the background job pool (a plain thread, not tied to any
    request) — opens its own short-lived DB session via SessionLocal()
    rather than reusing a request-scoped one, since by the time this runs
    the request that queued the job has already returned."""
    log_q = _LogQueue(progress_cb)

    as_on_date = date.fromisoformat(as_on_date_str) if as_on_date_str else date.today()

    if progress_cb:
        progress_cb(0.03, "Loading centralized mappings...")
    db = SessionLocal()
    try:
        incharge_map, supplier_site_map = mapping_store.load_all(db)
    finally:
        db.close()

    if progress_cb:
        progress_cb(0.08, "Reading register and fetching Oracle ERP data...")
    df, total_input_rows, unidentified_removed_count, df_unidentified, erp_ok = processor.process_report(
        input_path, log_q, as_on_date,
        oracle_cfg=_ORACLE_CFG, supplier_site_map=supplier_site_map,
    )

    if progress_cb:
        progress_cb(0.55, "Classifying advance-of-customer rows...")
    df_main, df_advance, _salesperson_map = processor.classify_advance_customers(
        df, ageing_path, log_q,
    )

    if progress_cb:
        progress_cb(0.75, "Validating mappings...")
    validation_errors = processor._validate_before_save(df_main, incharge_map, supplier_site_map)
    validation_warnings = [
        {"category": category, "items": items} for category, items in validation_errors
    ]
    for category, items in validation_errors:
        log_q.put(("warn", f"{category}: {len(items)} unmapped value(s)"))

    if progress_cb:
        progress_cb(0.86, "Writing formatted workbook...")
    processor.write_formatted_excel(
        df_main, df_advance, output_path, as_on_date,
        df_unidentified=df_unidentified, incharge_map=incharge_map, log_q=log_q,
    )

    try:
        Path(input_path).unlink(missing_ok=True)
        Path(ageing_path).unlink(missing_ok=True)
    except OSError:
        pass

    filename = f"Unapplied_Receipts_Report_As_On_{as_on_date.isoformat()}.xlsx"
    log_q.put(("ok", f"Done - {filename}"))
    if progress_cb:
        progress_cb(1.0, "Report ready")

    return {
        "output_path": str(output_path),
        "download_filename": filename,
        "as_on_date": as_on_date.isoformat(),
        "total_input_rows": total_input_rows,
        "unidentified_removed_count": unidentified_removed_count,
        "main_row_count": len(df_main),
        "advance_row_count": len(df_advance),
        "validation_warnings": validation_warnings,
        "oracle_ok": erp_ok,
        "log": log_q.messages,
    }


@router.post("/process")
async def submit_process(
    file: UploadFile = File(...),
    ageing_file: UploadFile = File(...),
    as_on_date: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """Real signature behind this: processor.process_report(input_path,
    log_q, as_on_date, oracle_cfg=, supplier_site_map=) followed by
    processor.classify_advance_customers(df, ageing_path, log_q) — ``file``
    is the Unapplied Receipts Register export, ``ageing_file`` is the
    Ageing export classify_advance_customers reads, and ``as_on_date`` is an
    optional ISO (YYYY-MM-DD) form field that defaults server-side to today
    (IST), same as process_report's own default when as_on_date is None."""
    if as_on_date:
        try:
            date.fromisoformat(as_on_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="as_on_date must be an ISO date (YYYY-MM-DD)")

    input_path = await save_upload(file, SCRATCH_DIR)
    ageing_path = await save_upload(ageing_file, SCRATCH_DIR)
    output_path = SCRATCH_DIR / f"{input_path.stem}_Unapplied_Receipts_Report.xlsx"

    job_id = submit_job(
        _run_process_job,
        str(input_path),
        str(ageing_path),
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

    filename = result.get("download_filename") or "Unapplied_Receipts_Report.xlsx"
    return FileResponse(path=str(output_path), filename=filename, media_type=_XLSX_MEDIA_TYPE)


# ── request bodies ───────────────────────────────────────────────────────────

class AccountsInchargeBody(BaseModel):
    location: str
    accounts_incharge: Optional[str] = None


class SupplierSiteBody(BaseModel):
    supplier_site: str
    location: Optional[str] = None


# ── 1. Accounts Incharge Map  (key = location) ─────────────────────────────

@router.get("/mappings/accounts-incharge")
def list_accounts_incharge(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    incharge_map, _ = mapping_store.load_all(db)
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


# ── 2. Supplier Site Map  (key = supplier_site) ─────────────────────────────

@router.get("/mappings/supplier-sites")
def list_supplier_sites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, supplier_site_map = mapping_store.load_all(db)
    return [
        {"supplier_site": supplier_site, "location": location}
        for supplier_site, location in sorted(supplier_site_map.items())
    ]


@router.post("/mappings/supplier-sites")
def add_supplier_site(body: SupplierSiteBody, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    supplier_site = body.supplier_site.strip()
    if not supplier_site:
        raise HTTPException(status_code=400, detail="supplier_site is required")
    location = (body.location or "").strip()
    mapping_store.upsert_supplier_site(db, supplier_site, location)
    return {"ok": True, "supplier_site": supplier_site, "location": location}


@router.put("/mappings/supplier-sites/{key}")
def edit_supplier_site(key: str, body: SupplierSiteBody, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    new_supplier_site = body.supplier_site.strip()
    location = (body.location or "").strip()

    if new_supplier_site != key:
        if not mapping_store.delete_supplier_site(db, key):
            raise HTTPException(status_code=404, detail="Supplier site not found")
    mapping_store.upsert_supplier_site(db, new_supplier_site, location)
    return {"ok": True, "supplier_site": new_supplier_site, "location": location}


@router.delete("/mappings/supplier-sites/{key}")
def delete_supplier_site(key: str, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    if not mapping_store.delete_supplier_site(db, key):
        raise HTTPException(status_code=404, detail="Supplier site not found")
    return {"ok": True}
