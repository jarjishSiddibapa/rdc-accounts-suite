"""Unaccounted Transactions Report tool routes.

Ports the desktop app's three report generators (Unaccounted Transactions,
Pending MRN, Uninvoiced Expense PO), the Supplier-Site -> Location ->
Accounts-Incharge mapping CRUD (mirrors ui_editor.py's MappingEditor tabs),
the "Mapping Not Found" fix-up flow (mirrors ui_widgets.py's
MissingMappingPopup: the user supplies only a Location, Accounts Incharge is
always derived from the Location<->Incharge table), and the combined
"generate all 3 reports & email them" workflow (mirrors ui_mail_panel.py's
MailPanel).
"""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import get_db
from app.jobs import (
    cancel_job,
    claim_job_action,
    finish_job_action,
    get_job,
    run_cpu_phase,
    submit_job,
)
from app.permissions import require_app_access
from app.regional import now_ist
from app.services import mailer_shared
from app.services.unaccounted import excel_writers, mappings, processing
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/unaccounted", tags=["unaccounted"],
    dependencies=[Depends(require_app_access("unaccounted"))],
)

# Upload size guard: refuse anything above this before it can fill up
# SCRATCH_DIR or blow up memory while reading the request body.
# ── Shared helpers ─────────────────────────────────────────────────────────────

class _LogQueue:
    """Adapts processing.py's ``log_q.put((level, msg))`` calls to the
    background job's optional ``progress_cb(frac, phase)`` callback, and
    keeps every message so it can be returned to the caller for display."""

    def __init__(self, progress_cb=None, start=0.05, end=0.85):
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


async def _save_upload(upload: UploadFile) -> Path:
    return await save_upload(upload, SCRATCH_DIR)


def _split_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _require_detected_periods(
    path: str | Path,
    report_label: str,
    detector: Callable[[str], list],
) -> list[str]:
    """Parse and require the period filter values for an MRN/PO upload.

    Period selection is part of report correctness, not optional metadata. A
    parser failure and a valid-looking file with no usable periods must both
    stop the workflow before a durable job is queued.
    """
    periods = [str(value).strip() for value in detector(str(path)) if str(value).strip()]
    if not periods:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No periods could be detected in the {report_label} file. "
                "Check that this is the correct ERP export and retry; report processing "
                "cannot continue until period detection succeeds."
            ),
        )
    return periods


def _validate_excluded_periods(
    excluded: set[str], detected: list[str], report_label: str
) -> None:
    unknown = sorted(excluded.difference(detected))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The selected {report_label} exclusions do not match the uploaded file: "
                f"{', '.join(unknown)}. Detect the file's periods again before processing."
            ),
        )


def _unlink_uploaded_paths(paths: list[str | Path | None]) -> None:
    for path in paths:
        if path:
            Path(path).unlink(missing_ok=True)


class _CollectingLogQueue:
    """Plain picklable log_q: just accumulates (level, message) tuples, no
    progress_cb wiring (which isn't picklable and couldn't cross a process
    boundary anyway). Used inside the CPU-phase wrapper functions below,
    which run in a subprocess via run_cpu_phase - the collected messages are
    handed back as part of that call's return value (a mutated side-channel
    object's state never crosses back from a subprocess on its own, only
    what the call actually returns does), so the detailed per-step log this
    tool's UI shows the user is preserved exactly as before."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def _cpu_phase_unaccounted(paths: list, output_path: str):
    log_q = _CollectingLogQueue()
    df, total_rows, input_cols, matched = processing.process_report_multi(paths, log_q)
    excel_writers.write_formatted_excel(df, output_path)
    return df, total_rows, input_cols, matched, log_q.messages


def _cpu_phase_mrn(path: str, exclude_periods: set, output_path: str):
    log_q = _CollectingLogQueue()
    df, total_rows, input_cols, matched = processing.process_mrn_report(path, exclude_periods, log_q)
    excel_writers.write_formatted_mrn_excel(df, output_path)
    return df, total_rows, input_cols, matched, log_q.messages


def _cpu_phase_po(path: str, exclude_months: set, keywords: list, fuzzy_threshold: float, output_path: str):
    log_q = _CollectingLogQueue()
    main_df, moved_df, unmapped_df, total_rows, input_cols, matched = processing.process_po_report(
        path, exclude_months, keywords, log_q, fuzzy_threshold
    )
    excel_writers.write_formatted_po_excel(main_df, moved_df, unmapped_df, output_path)
    return main_df, moved_df, unmapped_df, total_rows, input_cols, matched, log_q.messages


# ── Job bodies (run in the background thread pool; CPU-heavy work above runs
#    on the CPU process pool via run_cpu_phase for real multi-core throughput) ──

def _job_unaccounted(
    paths: list,
    output_path: str,
    download_name: str,
    progress_cb=None,
) -> dict:
    if progress_cb:
        progress_cb(0.05, "Processing report...")
    try:
        df, total_rows, input_cols, matched, log_messages = run_cpu_phase(
            _cpu_phase_unaccounted, paths, output_path
        )
    finally:
        for path in paths:
            Path(path).unlink(missing_ok=True)
    if progress_cb:
        progress_cb(0.95, "Report ready")
    unmapped = sorted(
        df[df["Location"].astype(str).str.strip() == ""]["Supplier Site"]
        .astype(str).unique().tolist()
    )
    return {
        "total_rows": total_rows,
        "input_cols": input_cols,
        "matched": matched,
        "unmatched": total_rows - matched,
        "unmapped_sites": unmapped,
        "output_path": output_path,
        "download_name": download_name,
        "log": log_messages,
    }


def _job_mrn(
    path: str,
    exclude_periods: set,
    output_path: str,
    download_name: str,
    progress_cb=None,
) -> dict:
    if progress_cb:
        progress_cb(0.05, "Processing report...")
    try:
        df, total_rows, input_cols, matched, log_messages = run_cpu_phase(
            _cpu_phase_mrn, path, exclude_periods, output_path
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if progress_cb:
        progress_cb(0.95, "Report ready")
    from app.services.unaccounted.constants import MRN_SITE_COL
    unmapped = sorted(
        df[df["Location"].astype(str).str.strip() == ""][MRN_SITE_COL]
        .astype(str).unique().tolist()
    )
    return {
        "total_rows": total_rows,
        "input_cols": input_cols,
        "matched": matched,
        "unmatched": total_rows - matched,
        "unmapped_sites": unmapped,
        "output_path": output_path,
        "download_name": download_name,
        "log": log_messages,
    }


def _job_po(
    path: str,
    exclude_months: set,
    keywords: list,
    fuzzy_threshold: float,
    output_path: str,
    download_name: str,
    progress_cb=None,
) -> dict:
    if progress_cb:
        progress_cb(0.05, "Processing report...")
    try:
        main_df, moved_df, unmapped_df, total_rows, input_cols, matched, log_messages = run_cpu_phase(
            _cpu_phase_po, path, exclude_months, keywords, fuzzy_threshold, output_path
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if progress_cb:
        progress_cb(0.95, "Report ready")
    from app.services.unaccounted.constants import PO_SITE_COL
    unmapped = (
        sorted(unmapped_df[PO_SITE_COL].astype(str).unique().tolist())
        if PO_SITE_COL in unmapped_df.columns else []
    )
    return {
        "total_rows": total_rows,
        "input_cols": input_cols,
        "matched": matched,
        "unmatched": total_rows - matched,
        "unmapped_sites": unmapped,
        "output_path": output_path,
        "download_name": download_name,
        "log": log_messages,
    }


# ── Report generation endpoints ────────────────────────────────────────────────

@router.post("/unaccounted/process")
async def process_unaccounted(
    files: list[UploadFile] = File(...),
    user=Depends(get_current_user),
):
    paths = [str(await _save_upload(f)) for f in files]
    stem = Path(files[0].filename or "unaccounted").stem
    output_path = str(SCRATCH_DIR / f"{uuid.uuid4()}_{stem}_unaccounted.xlsx")

    job_id = submit_job(
        _job_unaccounted,
        paths,
        output_path,
        f"{stem}_Unaccounted_Transactions.xlsx",
        owner_id=user.id,
    )
    return {"job_id": job_id}


@router.post("/mrn/process")
async def process_mrn(
    file: UploadFile = File(...),
    exclude_periods: str = Form(""),
    user=Depends(get_current_user),
):
    path = str(await _save_upload(file))
    stem = Path(file.filename or "mrn").stem
    output_path = str(SCRATCH_DIR / f"{uuid.uuid4()}_{stem}_mrn.xlsx")

    excluded = set(_split_csv(exclude_periods))
    try:
        detected = await run_in_threadpool(
            _require_detected_periods,
            path,
            "Pending MRN",
            processing.detect_mrn_periods,
        )
        _validate_excluded_periods(excluded, detected, "Pending MRN")
    except Exception:
        _unlink_uploaded_paths([path])
        raise

    job_id = submit_job(
        _job_mrn,
        path,
        excluded,
        output_path,
        f"{stem}_Pending_MRN.xlsx",
        owner_id=user.id,
    )
    return {"job_id": job_id}


@router.post("/po/process")
async def process_po(
    file: UploadFile = File(...),
    exclude_months: str = Form(""),
    keywords: str = Form(""),
    fuzzy_threshold: Optional[float] = Form(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    path = str(await _save_upload(file))
    stem = Path(file.filename or "po").stem
    output_path = str(SCRATCH_DIR / f"{uuid.uuid4()}_{stem}_po.xlsx")

    excluded = set(_split_csv(exclude_months))
    try:
        detected = await run_in_threadpool(
            _require_detected_periods,
            path,
            "Uninvoiced Expense PO",
            processing.detect_po_periods,
        )
        _validate_excluded_periods(excluded, detected, "Uninvoiced Expense PO")
    except Exception:
        _unlink_uploaded_paths([path])
        raise

    kw_list = _split_csv(keywords) or mappings._load_po_keywords(db)
    threshold = fuzzy_threshold if fuzzy_threshold is not None else mappings._load_po_threshold(db)

    job_id = submit_job(
        _job_po,
        path,
        excluded,
        kw_list,
        threshold,
        output_path,
        f"{stem}_Uninvoiced_Expense_PO.xlsx",
        owner_id=user.id,
    )
    return {"job_id": job_id}


# ── Period / PO-number detection helpers (needed to populate the exclude
#    filters before the user commits to a /process call) ─────────────────────

@router.post("/mrn/detect-periods")
async def detect_mrn_periods(file: UploadFile = File(...), user=Depends(get_current_user)):
    path = await _save_upload(file)
    try:
        periods = await run_in_threadpool(
            _require_detected_periods,
            path,
            "Pending MRN",
            processing.detect_mrn_periods,
        )
        return {"periods": periods}
    finally:
        path.unlink(missing_ok=True)


@router.post("/po/detect-periods")
async def detect_po_periods(file: UploadFile = File(...), user=Depends(get_current_user)):
    path = await _save_upload(file)
    try:
        periods = await run_in_threadpool(
            _require_detected_periods,
            path,
            "Uninvoiced Expense PO",
            processing.detect_po_periods,
        )
        numbers = await run_in_threadpool(processing.detect_po_numbers, str(path))
        return {"periods": periods, "po_numbers": numbers}
    finally:
        path.unlink(missing_ok=True)


# ── Job status / download ──────────────────────────────────────────────────────

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

    info = job.get("result") or {}
    if not isinstance(info, dict):
        raise HTTPException(status_code=404, detail="Output file not found")
    output_path = Path(info.get("output_path", ""))
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output file not found")

    download_name = info.get("download_name") or output_path.name
    return FileResponse(
        path=str(output_path),
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Missing-mapping fix-up (mirrors MissingMappingPopup) ───────────────────────

class SiteFixBody(BaseModel):
    supplier_site: str
    location: str


@router.post("/mappings/fix")
def fix_site_mapping(body: SiteFixBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Persist a single Supplier Site -> Location fix. Accounts Incharge is
    never supplied by the caller — it's derived from the Location <->
    Accounts Incharge map, exactly like the desktop app's popup.

    Upserts exactly this one row (see mappings._upsert_site_override)
    instead of loading the whole site-override table, changing one entry,
    and writing the whole table back: fixing several different unmapped
    sites back-to-back — the normal way this screen is used — used to race
    under that pattern, so whichever fix's save landed last silently
    soft-deleted every other fix an in-flight request had just saved.
    """
    incharge = mappings._load_location_incharge(db).get(body.location, "")
    mappings._upsert_site_override(body.supplier_site, body.location, incharge, db)
    return {
        "supplier_site": body.supplier_site,
        "location": body.location,
        "accounts_incharge": incharge,
    }


# ── Mapping CRUD: Supplier Site overrides ──────────────────────────────────────

class SiteOverrideBody(BaseModel):
    supplier_site: str
    location: str
    accounts_incharge: str = ""


@router.get("/mappings/site-overrides")
def list_site_overrides(user=Depends(get_current_user), db: Session = Depends(get_db)):
    site_overrides, _ = mappings._load_custom_mappings(db)
    return [
        {"supplier_site": k, "location": v[0], "accounts_incharge": v[1]}
        for k, v in sorted(site_overrides.items())
    ]


@router.post("/mappings/site-overrides")
def create_site_override(body: SiteOverrideBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    mappings._upsert_site_override(body.supplier_site, body.location, body.accounts_incharge, db)
    return {"ok": True}


@router.put("/mappings/site-overrides/{supplier_site}")
def update_site_override(
    supplier_site: str, body: SiteOverrideBody,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    if supplier_site != body.supplier_site:
        if not mappings._delete_site_override(supplier_site, db):
            raise HTTPException(status_code=404, detail="Site override not found")
    mappings._upsert_site_override(body.supplier_site, body.location, body.accounts_incharge, db)
    return {"ok": True}


@router.delete("/mappings/site-overrides/{supplier_site}")
def delete_site_override(supplier_site: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not mappings._delete_site_override(supplier_site, db):
        raise HTTPException(status_code=404, detail="Site override not found")
    return {"ok": True}


@router.get("/mappings/site-overrides/archived")
def list_archived_site_overrides(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return sorted(mappings._list_archived_site_overrides(db), key=lambda r: r["supplier_site"])


@router.post("/mappings/site-overrides/{supplier_site}/restore")
def restore_site_override(supplier_site: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not mappings._restore_site_override(supplier_site, db):
        raise HTTPException(status_code=404, detail="Archived site override not found")
    return {"ok": True}


# ── Mapping CRUD: Created-By mapping ────────────────────────────────────────────

class CreatorMappingBody(BaseModel):
    created_by: str
    location: str
    accounts_incharge: str = ""


@router.get("/mappings/creator")
def list_creator_mappings(user=Depends(get_current_user), db: Session = Depends(get_db)):
    _, creator_map = mappings._load_custom_mappings(db)
    return [
        {"created_by": k, "location": v[0], "accounts_incharge": v[1]}
        for k, v in sorted(creator_map.items())
    ]


@router.post("/mappings/creator")
def create_creator_mapping(body: CreatorMappingBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    mappings._upsert_creator_mapping(body.created_by, body.location, body.accounts_incharge, db)
    return {"ok": True}


@router.put("/mappings/creator/{created_by}")
def update_creator_mapping(
    created_by: str, body: CreatorMappingBody,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    if created_by != body.created_by:
        if not mappings._delete_creator_mapping(created_by, db):
            raise HTTPException(status_code=404, detail="Creator mapping not found")
    mappings._upsert_creator_mapping(body.created_by, body.location, body.accounts_incharge, db)
    return {"ok": True}


@router.delete("/mappings/creator/{created_by}")
def delete_creator_mapping(created_by: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not mappings._delete_creator_mapping(created_by, db):
        raise HTTPException(status_code=404, detail="Creator mapping not found")
    return {"ok": True}


@router.get("/mappings/creator/archived")
def list_archived_creator_mappings(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return sorted(mappings._list_archived_creator_mappings(db), key=lambda r: r["created_by"])


@router.post("/mappings/creator/{created_by}/restore")
def restore_creator_mapping(created_by: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not mappings._restore_creator_mapping(created_by, db):
        raise HTTPException(status_code=404, detail="Archived creator mapping not found")
    return {"ok": True}


# ── Mapping CRUD: Location <-> Accounts Incharge (one-time table) ─────────────

class LocationInchargeBody(BaseModel):
    location: str
    accounts_incharge: str


@router.get("/mappings/location-incharge")
def list_location_incharge(user=Depends(get_current_user), db: Session = Depends(get_db)):
    loc_inc = mappings._load_location_incharge(db)
    return [
        {"location": k, "accounts_incharge": v}
        for k, v in sorted(loc_inc.items())
    ]


@router.post("/mappings/location-incharge")
def create_location_incharge(body: LocationInchargeBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    mappings._upsert_location_incharge(body.location, body.accounts_incharge, db)
    return {"ok": True}


@router.put("/mappings/location-incharge/{location}")
def update_location_incharge(
    location: str, body: LocationInchargeBody,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    if location != body.location:
        if not mappings._delete_location_incharge(location, db):
            raise HTTPException(status_code=404, detail="Location not found")
    mappings._upsert_location_incharge(body.location, body.accounts_incharge, db)
    return {"ok": True}


@router.delete("/mappings/location-incharge/{location}")
def delete_location_incharge(location: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not mappings._delete_location_incharge(location, db):
        raise HTTPException(status_code=404, detail="Location not found")
    return {"ok": True}


@router.get("/mappings/location-incharge/archived")
def list_archived_location_incharge(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return sorted(mappings._list_archived_location_incharge(db), key=lambda r: r["location"])


@router.post("/mappings/location-incharge/{location}/restore")
def restore_location_incharge(location: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not mappings._restore_location_incharge(location, db):
        raise HTTPException(status_code=404, detail="Archived location not found")
    return {"ok": True}


@router.get("/mappings/known-locations")
def known_locations(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return mappings._known_locations(db)


@router.get("/mappings/known-incharges")
def known_incharges(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return mappings._known_incharges(db)


# ── PO keyword / threshold endpoints ───────────────────────────────────────────

class PoKeywordsBody(BaseModel):
    keywords: list[str]
    threshold: float = 0.82


@router.get("/po/keywords")
def get_po_keywords(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "keywords": mappings._load_po_keywords(db),
        "threshold": mappings._load_po_threshold(db),
    }


@router.put("/po/keywords")
def put_po_keywords(body: PoKeywordsBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not 0.50 <= body.threshold <= 1.00:
        raise HTTPException(status_code=400, detail="threshold must be between 0.50 and 1.00")
    mappings._save_po_keywords(body.keywords, db)
    mappings._save_po_threshold(body.threshold, db)
    return {"keywords": mappings._load_po_keywords(db), "threshold": mappings._load_po_threshold(db)}


# ── Excluded PO numbers ────────────────────────────────────────────────────────

class ExcludedPoBody(BaseModel):
    po_number: str


@router.get("/po/excluded")
def get_excluded_pos(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return mappings._load_po_excluded(db)


@router.post("/po/excluded")
def add_excluded_po(body: ExcludedPoBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    po_number = body.po_number.strip()
    if not po_number:
        raise HTTPException(status_code=400, detail="po_number is required")
    excluded = mappings._load_po_excluded(db)
    if po_number not in excluded:
        excluded.append(po_number)
        mappings._save_po_excluded(excluded, db)
    return mappings._load_po_excluded(db)


@router.put("/po/excluded/{po_number}")
def edit_excluded_po(
    po_number: str,
    body: ExcludedPoBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    replacement = body.po_number.strip()
    if not replacement:
        raise HTTPException(status_code=400, detail="po_number is required")
    excluded = mappings._load_po_excluded(db)
    if po_number not in excluded:
        raise HTTPException(status_code=404, detail="Excluded PO number not found")
    if replacement != po_number and replacement in excluded:
        raise HTTPException(status_code=409, detail="That PO number is already excluded")
    excluded[excluded.index(po_number)] = replacement
    mappings._save_po_excluded(excluded, db)
    return mappings._load_po_excluded(db)


@router.delete("/po/excluded")
def clear_excluded_pos(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Archive every active exclusion; rows remain recoverable in the database."""
    mappings._save_po_excluded([], db)
    return []


@router.delete("/po/excluded/{po_number}")
def remove_excluded_po(po_number: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    excluded = [p for p in mappings._load_po_excluded(db) if p != po_number]
    mappings._save_po_excluded(excluded, db)
    return excluded


# ── Combined "generate all 3 reports & email" workflow (mirrors MailPanel) ────

@router.get("/mail/defaults")
def get_mail_defaults(db: Session = Depends(get_db)):
    """Return the desktop mailer's original editable starting values.

    Recipient rows remain admin-managed in the centralized database; the
    subject and intro are generated with the same helpers used for the final
    email so the compose form and send-time preview cannot drift apart.
    """
    default_month = now_ist().strftime("%b-%y")
    recipients = mailer_shared.get_report_recipient_defaults(db, "unaccounted")
    return {
        "to": recipients["to"],
        "cc": recipients["cc"],
        "default_month": default_month,
        "subject": mailer_shared.build_default_subject(default_month),
        "intro": mailer_shared.build_default_intro_text(
            month_ua=default_month,
            month_mrn=default_month,
            month_po=default_month,
        ),
        "include_ua": True,
        "include_mrn": True,
        "include_po": True,
    }

def _job_mail_send(
    user_id: int,
    ua_paths: list,
    mrn_path: Optional[str],
    po_path: Optional[str],
    exclude_periods: set,
    exclude_months: set,
    keywords: list,
    fuzzy_threshold: float,
    month_subject: str,
    month_ua: str,
    month_mrn: str,
    month_po: str,
    as_on_date: str,
    include_ua: bool,
    include_mrn: bool,
    include_po: bool,
    custom_subject: str,
    custom_intro: str,
    to_list: list,
    cc_list: list,
    force_send: bool,
    progress_cb=None,
) -> dict:
    log_q = _LogQueue(progress_cb)
    unmapped_all: dict[str, list] = {}
    ua_out = mrn_out = po_out = None
    output_dir = SCRATCH_DIR / f"mail-{uuid.uuid4()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_date = (as_on_date or now_ist().strftime("%d.%m.%Y"))
    safe_date = safe_date.replace("/", ".").replace("\\", ".").replace(":", ".")
    safe_month_ua = month_ua.strip() or "Report"
    safe_month_mrn = month_mrn.strip() or "Report"
    safe_month_po = month_po.strip() or "Report"

    try:
        if include_ua and ua_paths:
            df_ua, *_ = processing.process_report_multi(ua_paths, log_q)
            unmapped = sorted(
                df_ua[df_ua["Location"].astype(str).str.strip() == ""]["Supplier Site"]
                .astype(str).unique().tolist()
            )
            if unmapped:
                unmapped_all["unaccounted"] = unmapped
            ua_out = str(output_dir / f"Unaccounted {safe_month_ua} as on {safe_date}.xlsx")
            excel_writers.write_formatted_excel(df_ua, ua_out)

        if include_mrn and mrn_path:
            df_mrn, *_ = processing.process_mrn_report(mrn_path, exclude_periods, log_q)
            from app.services.unaccounted.constants import MRN_SITE_COL
            unmapped = sorted(
                df_mrn[df_mrn["Location"].astype(str).str.strip() == ""][MRN_SITE_COL]
                .astype(str).unique().tolist()
            )
            if unmapped:
                unmapped_all["mrn"] = unmapped
            mrn_out = str(output_dir / f"Pending MRN till {safe_month_mrn} as on {safe_date}.xlsx")
            excel_writers.write_formatted_mrn_excel(df_mrn, mrn_out)

        if include_po and po_path:
            main_df, moved_df, unmapped_df, *_ = processing.process_po_report(
                po_path, exclude_months, keywords, log_q, fuzzy_threshold
            )
            from app.services.unaccounted.constants import PO_SITE_COL
            unmapped = (
                sorted(unmapped_df[PO_SITE_COL].astype(str).unique().tolist())
                if PO_SITE_COL in unmapped_df.columns else []
            )
            if unmapped:
                unmapped_all["po"] = unmapped
            po_out = str(output_dir / f"Uninvoiced Expense Report till {safe_month_po} as on {safe_date}.xlsx")
            excel_writers.write_formatted_po_excel(main_df, moved_df, unmapped_df, po_out)
    finally:
        # Inputs are fully consumed by this point and never read again.
        # output_dir is NOT cleaned up here - its 3 generated reports are
        # still needed later by /mail/confirm-send and /mail/download, an
        # indeterminate time after this job finishes (or possibly never, if
        # the user only downloads reports without ever sending). It's
        # reclaimed by the scheduled scratch sweep instead (see
        # app/scheduler.py's _sweep_scratch, which gives "mail-*" directories
        # their own generous 24h grace period rather than the shorter
        # general-purpose scratch_cleanup_minutes - a real review-then-send
        # can take a while, and reaping it too early once caused a send with
        # missing attachments).
        for path in ua_paths:
            Path(path).unlink(missing_ok=True)
        if mrn_path:
            Path(mrn_path).unlink(missing_ok=True)
        if po_path:
            Path(po_path).unlink(missing_ok=True)

    if unmapped_all and not force_send:
        return {
            "status": "needs_mapping_fix",
            "unmapped_sites": unmapped_all,
            "output_paths": {"unaccounted": ua_out, "mrn": mrn_out, "po": po_out},
            "log": log_q.messages,
        }

    settings = mailer_shared.get_email_settings(user_id)
    if not settings.get("configured"):
        return {
            "status": "email_not_configured",
            "message": "You haven't set up your email sender yet — go to Settings.",
            "unmapped_sites": unmapped_all,
            "output_paths": {"unaccounted": ua_out, "mrn": mrn_out, "po": po_out},
            "log": log_q.messages,
        }
    subject, html_body = mailer_shared.build_email_content(
        unaccounted_path=ua_out or "",
        mrn_path=mrn_out or "",
        po_path=po_out or "",
        month_subject=month_subject,
        month_unaccounted=month_ua,
        month_mrn=month_mrn,
        month_po=month_po,
        signature=settings.get("signature", ""),
        include_ua=include_ua,
        include_mrn=include_mrn,
        include_po=include_po,
        custom_subject=custom_subject,
        custom_intro=custom_intro,
    )
    # Use exactly what the user had in the To/Cc fields when they clicked
    # generate - no silent fallback to the admin-managed defaults. The
    # frontend already prefills these fields from the defaults on load, so an
    # empty list here means the user deliberately cleared the field (e.g. a
    # genuinely CC-less send), not that the field was never filled in.
    final_to = to_list
    final_cc = cc_list
    attachments = [p for p in [ua_out, mrn_out, po_out] if p]

    # Always stop here and hand back the fully-built email for review first -
    # nothing gets sent from this job. The frontend shows the user exactly
    # what will go out (subject/body/to/cc/from), and only a separate,
    # explicit call to /mail/confirm-send actually dispatches it.
    return {
        "status": "preview",
        "subject": subject,
        "html_body": html_body,
        "from_email": settings["email"],
        "to": final_to,
        "cc": final_cc,
        "attachments": attachments,
        "output_paths": {"unaccounted": ua_out, "mrn": mrn_out, "po": po_out},
        "unmapped_sites": unmapped_all,
        "log": log_q.messages,
    }


class ConfirmSendBody(BaseModel):
    job_id: str


@router.get("/mail/download/{job_id}/{report_key}")
def download_mail_report(
    job_id: str,
    report_key: str,
    user=Depends(get_current_user),
):
    """Save one report produced by the combined desktop-style mail workflow."""
    if report_key not in {"unaccounted", "mrn", "po"}:
        raise HTTPException(status_code=404, detail="Report not found")
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="Job is not finished yet")
    result = job.get("result") or {}
    output_path = Path((result.get("output_paths") or {}).get(report_key) or "")
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Generated report not found")
    return FileResponse(
        path=str(output_path),
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/mail/confirm-send")
def mail_confirm_send(
    body: ConfirmSendBody,
    user=Depends(get_current_user),
):
    """The only endpoint that actually dispatches an email. Requires a
    completed /mail/send job whose preview the user has already seen -
    re-sends nothing, re-generates nothing, just takes the exact
    already-built subject/body/to/cc/attachments from that job's result and
    hands it to SMTP. Rejects anything that isn't a 'preview' result, so a
    stale or tampered job_id can't be used to send something the user never
    reviewed."""
    job = get_job(body.job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    result = job.get("result") or {}
    if job.get("status") != "done" or result.get("status") != "preview":
        raise HTTPException(
            status_code=400,
            detail="This job isn't a ready-to-send preview. Generate the preview again.",
        )

    settings = mailer_shared.get_email_settings(user.id)
    if not settings.get("configured"):
        raise HTTPException(
            status_code=400,
            detail="You haven't set up your email sender yet — go to Settings.",
        )

    claim_state, _ = claim_job_action(
        body.job_id,
        owner_id=user.id,
        action="confirm-send",
    )
    if claim_state != "claimed":
        detail = {
            "in_progress": "This email is already being sent from another tab.",
            "completed": "This preview has already been sent.",
            "failed": (
                "The previous send attempt did not complete safely. Generate a fresh "
                "preview before trying again to avoid duplicate email."
            ),
        }.get(claim_state, "Job not found.")
        raise HTTPException(status_code=404 if claim_state == "missing" else 409, detail=detail)

    try:
        mailer_shared.send_mail(
            from_email=settings["email"],
            app_password=settings["app_password"],
            to_addresses=result["to"],
            cc_addresses=result["cc"],
            subject=result["subject"],
            html_body=result["html_body"],
            attachments=result["attachments"],
        )
    except mailer_shared.MailAttachmentError as exc:
        finish_job_action(
            body.job_id,
            owner_id=user.id,
            action="confirm-send",
            succeeded=False,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        finish_job_action(
            body.job_id,
            owner_id=user.id,
            action="confirm-send",
            succeeded=False,
        )
        raise
    finish_job_action(
        body.job_id,
        owner_id=user.id,
        action="confirm-send",
        succeeded=True,
    )

    return {
        "status": "sent",
        "subject": result["subject"],
        "to": result["to"],
        "cc": result["cc"],
        "attachments": result["attachments"],
    }


@router.post("/mail/send")
async def mail_send(
    ua_files: list[UploadFile] = File(default=[]),
    mrn_file: Optional[UploadFile] = File(default=None),
    po_file: Optional[UploadFile] = File(default=None),
    exclude_periods: str = Form(""),
    exclude_months: str = Form(""),
    keywords: str = Form(""),
    fuzzy_threshold: Optional[float] = Form(None),
    month_subject: str = Form(""),
    month_ua: str = Form(""),
    month_mrn: str = Form(""),
    month_po: str = Form(""),
    as_on_date: str = Form(""),
    include_ua: bool = Form(True),
    include_mrn: bool = Form(True),
    include_po: bool = Form(True),
    custom_subject: str = Form(""),
    custom_intro: str = Form(""),
    to: str = Form(""),
    cc: str = Form(""),
    force_send: bool = Form(False),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate the Unaccounted Transactions / Pending MRN / Uninvoiced
    Expense PO reports together and build the exact email that WOULD be
    sent — subject, HTML body, from/to/cc, attachments — but never actually
    sends it. This is step 1 of 2: the frontend shows the user this preview
    so they can check everything is correct, then a separate, explicit call
    to POST /mail/confirm-send (with this job's id) is what actually
    dispatches it. Nothing about this endpoint can send an email on its own.

    Credentials come from the logged-in user's OWN Settings row
    (mailer_shared.get_email_settings(user.id)) — each user sends from their
    own configured Gmail identity, never a shared one. If that identity
    isn't configured yet, this fails fast with a clear message instead of
    running the (potentially slow) report generation first. If any report
    has unmapped Supplier Sites and force_send is not set, the job stops
    before building the preview and returns the unmapped sites so the
    frontend can run the same fix-up flow as /mappings/fix, then resubmit
    with force_send=true."""
    if not mailer_shared.get_email_settings(user.id).get("configured"):
        raise HTTPException(
            status_code=400,
            detail="You haven't set up your email sender yet — go to Settings.",
        )

    if not any((include_ua, include_mrn, include_po)):
        raise HTTPException(status_code=422, detail="Select at least one report to include.")
    if include_ua and not ua_files:
        raise HTTPException(status_code=422, detail="Upload the Unaccounted Transactions export files.")
    if include_mrn and mrn_file is None:
        raise HTTPException(status_code=422, detail="Upload the Pending MRN export file.")
    if include_po and po_file is None:
        raise HTTPException(status_code=422, detail="Upload the Uninvoiced Expense PO export file.")

    ua_paths = [str(await _save_upload(f)) for f in ua_files] if include_ua else []
    mrn_path = str(await _save_upload(mrn_file)) if (include_mrn and mrn_file) else None
    po_path = str(await _save_upload(po_file)) if (include_po and po_file) else None

    excluded_periods_set = set(_split_csv(exclude_periods))
    excluded_months_set = set(_split_csv(exclude_months))
    try:
        if mrn_path:
            detected_mrn = await run_in_threadpool(
                _require_detected_periods,
                mrn_path,
                "Pending MRN",
                processing.detect_mrn_periods,
            )
            _validate_excluded_periods(excluded_periods_set, detected_mrn, "Pending MRN")
        if po_path:
            detected_po = await run_in_threadpool(
                _require_detected_periods,
                po_path,
                "Uninvoiced Expense PO",
                processing.detect_po_periods,
            )
            _validate_excluded_periods(excluded_months_set, detected_po, "Uninvoiced Expense PO")
    except Exception:
        _unlink_uploaded_paths([*ua_paths, mrn_path, po_path])
        raise

    kw_list = _split_csv(keywords) or mappings._load_po_keywords(db)
    threshold = fuzzy_threshold if fuzzy_threshold is not None else mappings._load_po_threshold(db)

    job_id = submit_job(
        _job_mail_send,
        user.id,
        ua_paths, mrn_path, po_path,
        excluded_periods_set, excluded_months_set,
        kw_list, threshold,
        month_subject, month_ua, month_mrn, month_po, as_on_date,
        include_ua, include_mrn, include_po,
        custom_subject, custom_intro,
        _split_csv(to), _split_csv(cc),
        force_send,
        owner_id=user.id,
    )
    return {"job_id": job_id}
