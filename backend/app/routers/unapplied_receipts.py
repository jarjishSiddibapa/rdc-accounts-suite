"""RDC Unapplied Receipts Report tool routes.

Ports the desktop app's flow onto the shared FastAPI backend (see
``app.services.unapplied_receipts.processor`` for the full pipeline
docstring):

  1. POST /process          – upload the Unapplied Receipts Register export
                               (+ an optional "as on" date; the ageing is
                               fetched from the ERP), run process_report -> classify_advance
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

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from app.jobs import cancel_job, get_job, run_cpu_phase, submit_job
from app.models import User
from app.permissions import require_app_access
from app.services import customer_region
from app.services.erp_ageing import TYPE_COL, EitherEvent, fetch_ageing_from_erp
from app.services.unapplied_receipts.erp_register import fetch_register_from_erp
from app.services.unapplied_receipts import mapping_store, processor, report_job
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


# The log adapters and the two CPU-pool steps are shared with the Debtors Reports
# Auto Mailer (see app.services.unapplied_receipts.report_job).
_LogQueue = report_job.LogQueue
_CollectingLogQueue = report_job.CollectingLogQueue
_cpu_phase_classify_and_validate = report_job.cpu_classify_and_validate
_cpu_phase_write_report = report_job.cpu_write_report


# ── /process job pipeline ───────────────────────────────────────────────────

def _run_process_job(input_path: Optional[str], ageing_path: Optional[str], output_path: str,
                      as_on_date_str: Optional[str], allow_unmapped: bool = False,
                      progress_cb=None, cancel_event=None) -> dict:
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

    # The ERP work that does not depend on the register - every customer's region
    # and, when no ageing file was uploaded, the open ageing - starts now, on
    # connections of their own, while the register is read and its receipts are
    # looked up. They are waited for only where the result is needed.
    stop_background = threading.Event()
    background_cancel = EitherEvent(cancel_event, stop_background)
    regions = customer_region.RegionFetch(_ORACLE_CFG, background_cancel)
    ageing_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="erp-ageing-job")
    ageing_job = None
    if not ageing_path:
        # the open ageing comes straight from the ERP, so the advance-customer
        # check is always against today's data
        ageing_job = ageing_pool.submit(
            fetch_ageing_from_erp, _ORACLE_CFG, None, None, background_cancel, False,
        )

    try:
        # No register uploaded: it comes straight from the ERP (the ageing and the regions are
        # already being fetched alongside).
        register_frame = None
        if not input_path:
            if progress_cb:
                progress_cb(0.08, "Fetching the Unapplied Receipts Register from the ERP...")
            register_frame = fetch_register_from_erp(_ORACLE_CFG, log_q, background_cancel)
        elif progress_cb:
            progress_cb(0.08, "Reading register and fetching Oracle ERP data...")
        df, total_input_rows, unidentified_removed_count, df_unidentified, erp_ok = processor.process_report(
            input_path or "", log_q, as_on_date,
            oracle_cfg=_ORACLE_CFG, supplier_site_map=supplier_site_map,
            cancel_event=cancel_event, region_lookup=lambda: regions.result(log_q),
            register_frame=register_frame,
        )

        ageing_frame = None
        if ageing_job is not None:
            if progress_cb:
                progress_cb(0.45, "Waiting for the open ageing from the ERP...")
            ageing_frame = ageing_job.result()
            log_q.put(("ok", f"Fetched {len(ageing_frame):,} open ageing rows from the ERP"))
            # only these three columns are read; a slimmer frame is quicker to hand to the CPU process
            ageing_frame = ageing_frame[["Customer Account", TYPE_COL, "Sales Person Name"]]

        if progress_cb:
            progress_cb(0.55, "Classifying and validating...")
        # Pure pandas CPU work, no Oracle/DB - runs on the CPU process pool
        # for real multi-core throughput.
        df_main, df_advance, validation_warnings, cpu_log_messages = run_cpu_phase(
            _cpu_phase_classify_and_validate,
            df, ageing_path, incharge_map, supplier_site_map, ageing_frame,
        )
        log_q.messages.extend(cpu_log_messages)

        if validation_warnings and allow_unmapped:
            log_q.put(("warn", "Writing the report anyway - unmapped values stay blank."))
        if validation_warnings and not allow_unmapped:
            log_q.put(("warn", "Fix the mappings above and regenerate - report was not written yet."))
            return {
                "needs_mapping_fix": True,
                "total_input_rows": total_input_rows,
                "unidentified_removed_count": unidentified_removed_count,
                "main_row_count": len(df_main),
                "advance_row_count": len(df_advance),
                "validation_warnings": validation_warnings,
                "oracle_ok": erp_ok,
                "log": log_q.messages,
            }

        if progress_cb:
            progress_cb(0.65, "Writing the workbook...")
        cpu_log_messages = run_cpu_phase(
            _cpu_phase_write_report,
            df_main, df_advance, output_path, as_on_date, df_unidentified, incharge_map,
            progress_cb=progress_cb,
        )
        log_q.messages.extend(cpu_log_messages)
    finally:
        stop_background.set()                 # anything still running is not needed any more
        ageing_pool.shutdown(wait=True)
        if input_path:
            Path(input_path).unlink(missing_ok=True)
        if ageing_path:
            Path(ageing_path).unlink(missing_ok=True)

    filename = f"Unapplied_Receipts_Report_As_On_{as_on_date.isoformat()}.xlsx"
    log_q.put(("ok", f"Done - {filename}"))
    if progress_cb:
        progress_cb(1.0, "Report ready")

    return {
        "needs_mapping_fix": False,
        "allowed_unmapped": bool(validation_warnings),
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
    file: Optional[UploadFile] = File(None),
    ageing_file: Optional[UploadFile] = File(None),
    as_on_date: Optional[str] = Form(None),
    allow_unmapped: bool = Form(False),
    user: User = Depends(get_current_user),
):
    """Real signature behind this: processor.process_report(input_path,
    log_q, as_on_date, oracle_cfg=, supplier_site_map=) followed by
    processor.classify_advance_customers(df, ageing_path, log_q) — ``file``
    is the Unapplied Receipts Register export, ``ageing_file`` is an optional
    Ageing export (when it is left out, the open ageing is fetched live from
    the ERP), and ``as_on_date`` is an
    optional ISO (YYYY-MM-DD) form field that defaults server-side to today
    (IST), same as process_report's own default when as_on_date is None."""
    if as_on_date:
        try:
            date.fromisoformat(as_on_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="as_on_date must be an ISO date (YYYY-MM-DD)")

    # The register is fetched live from the ERP; an uploaded register export is used instead only if given.
    input_path = await save_upload(file, SCRATCH_DIR) if file is not None and file.filename else None
    ageing_path = (
        await save_upload(ageing_file, SCRATCH_DIR) if ageing_file is not None and ageing_file.filename else None
    )
    output_path = SCRATCH_DIR / f"{(input_path.stem if input_path else uuid.uuid4().hex)}_Unapplied_Receipts_Report.xlsx"

    job_id = submit_job(
        _run_process_job,
        str(input_path) if input_path else None,
        str(ageing_path) if ageing_path else None,
        str(output_path),
        as_on_date,
        allow_unmapped=allow_unmapped,
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
    if not output_path.is_file():
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
