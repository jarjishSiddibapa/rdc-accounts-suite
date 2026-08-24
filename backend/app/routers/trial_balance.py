"""Trial Balance — Location-wise Report Automation tool routes.

Ports the desktop app's flow (see the sibling source repo's main.py /
processor.py / mapping_store.py) onto the shared FastAPI backend:

  1. POST /accounts          – upload the Detail Trial Balance HTML/XLS
                               export, parse it just enough to return the
                               distinct (Account Code, Description) pairs
                               for the frontend's account-picker step
                               (mirrors the desktop app's blocking
                               _AccountPickerDialog, now a two-step API).
                               The parsed rows are stashed server-side keyed
                               by a short-lived token (same pattern as
                               app.routers.dms_downloader's in-memory
                               token stash) so /process doesn't need the
                               file re-uploaded or re-parsed.
  2. POST /process           – takes that token + the user's selected
                               Account Codes, runs process_report/
                               to_excel_bytes as a background job.
  3. GET  /jobs/{job_id}     – poll job status/progress.
  4. GET  /download/{job_id} – download the finished .xlsx once done.
  5. POST /mappings/fix      – the REST equivalent of the desktop app's
                               "Mapping Not Found" popup's "Update Mapping"
                               dialog: set Location Name + Region for one
                               Location Code (both fields required, exactly
                               like the original _EntryDialog).
  6. Four CRUD route groups, one per mapping table (location-codes,
     location-region, region-incharge, account-ho) — all backed by the
     shared MySQL database (see services/trial_balance/models.py), not an
     xlsx file.

No bulk mapping import/export routes: rdc_payables.py (the sibling tool
already ported onto this suite) has none either, and its
services/rdc_payables/mapping_store.py explicitly documents that the
bundled workbook is a one-way deployment seed only — "there is no
user-facing import/export" — confirming this was a deliberate suite-wide
removal, not an oversight. This tool follows the same rule.

This tool has no email feature in the original desktop app, so none is
added here.
"""

import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import SessionLocal, get_db
from app.jobs import get_job, submit_job
from app.models import User
from app.permissions import require_app_access
from app.regional import format_indian_number, now_ist
from app.services.trial_balance import mapping_store, processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/trial-balance", tags=["trial-balance"],
    dependencies=[Depends(require_app_access("trial-balance"))],
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# ── in-memory token stash for the upload -> account-picker -> process flow ──
# token -> {"path": str, "columns": list, "rows": list, "ts": float, "owner_id": int}
# Mirrors app.routers.dms_downloader's `_fetched` pattern: short-lived,
# in-process, pruned on access, scoped to the owning user.
_ACCOUNTS_TTL_SECONDS = 30 * 60
_stashed: dict[str, dict] = {}
_stash_lock = threading.Lock()


def _prune_stash() -> None:
    now = time.time()
    expired = [token for token, entry in _stashed.items() if now - entry["ts"] > _ACCOUNTS_TTL_SECONDS]
    for token in expired:
        entry = _stashed.pop(token, None)
        if entry:
            Path(entry["path"]).unlink(missing_ok=True)


# ── request bodies ─────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    token: str
    account_codes: list[str] = []


class MappingFixRequest(BaseModel):
    location_code: str
    location_name: str
    region: str


class LocationCodeBody(BaseModel):
    location_code: str
    location_name: str = ""


class LocationRegionBody(BaseModel):
    location_name: str
    region: str = ""


class RegionInchargeBody(BaseModel):
    region: str
    accounts_incharge: str = ""


class AccountHoBody(BaseModel):
    account_code: str
    ho_person: str = ""


# ── 1. Upload + account picker (POST /accounts) ─────────────────────────────

@router.post("/accounts")
async def parse_accounts(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """Parse the uploaded Detail Trial Balance export and return every
    distinct (Account Code, Description) pair found, for the frontend's
    account-picker step. The parsed rows are kept server-side under a
    token so POST /process can reuse them without re-uploading."""
    input_path = await save_upload(file, SCRATCH_DIR)
    try:
        columns, rows = processor.parse_html_report(str(input_path))
    except Exception:
        input_path.unlink(missing_ok=True)
        raise

    pairs = processor.distinct_account_descriptions(columns, rows)
    token = str(uuid.uuid4())
    with _stash_lock:
        _prune_stash()
        _stashed[token] = {
            "path": str(input_path),
            "columns": columns,
            "rows": rows,
            "ts": time.time(),
            "owner_id": user.id,
        }

    return {
        "token": token,
        "raw_row_count": len(rows),
        "accounts": [{"account_code": code, "description": desc} for code, desc in pairs],
    }


# ── 2. /process job pipeline ────────────────────────────────────────────────

def _run_process_job(input_path: str, columns: list, rows: list,
                      pivot_accounts: list, output_path: str,
                      progress_cb=None) -> dict:
    """Runs in the background job pool — mirrors the desktop app's
    _worker() in main.py, including how the "Mapping Not Found" /
    missing-codes list is computed.

    Opens its own short-lived DB session via SessionLocal() rather than
    reusing a FastAPI-injected one: by the time this function actually
    runs, the request that queued the job has already returned and its
    Depends(get_db) session has been closed."""
    log: list[str] = []

    def add_log(level: str, message: str) -> None:
        stamp = now_ist().strftime("%H:%M:%S")
        log.append(f"[{level}] [{stamp}] {message}")

    if progress_cb:
        progress_cb(0.05, "Loading centralized mappings...")
    add_log("INFO", f"Starting: {Path(input_path).name}")

    db = SessionLocal()
    try:
        loc_name_map, region_incharge_map, name_region_map, account_ho_map = mapping_store.load_all(db)
    finally:
        db.close()

    raw_row_count = len(rows)
    add_log("OK", f"Parsed {format_indian_number(raw_row_count)} rows")

    if progress_cb:
        progress_cb(0.35, "Deriving Location Code and cascading mappings...")
    df, missing_codes = processor.process_report(
        columns, rows, loc_name_map, region_incharge_map, name_region_map, account_ho_map
    )
    add_log("OK", f"Output rows: {format_indian_number(len(df))}")

    if missing_codes:
        add_log("WARN", f"Unmapped location codes: {format_indian_number(len(missing_codes))}")

    matched_count = int((df["Region"] != "").sum())
    unmatched_count = len(df) - matched_count

    if progress_cb:
        progress_cb(0.7, "Generating Excel output...")
    add_log("INFO", "Generating Excel output...")
    xlsx_bytes = processor.to_excel_bytes(df, pivot_accounts if pivot_accounts else None)
    Path(output_path).write_bytes(xlsx_bytes)

    try:
        Path(input_path).unlink(missing_ok=True)
    except OSError:
        pass

    filename = f"{Path(input_path).stem}_Location_Report.xlsx"
    add_log("OK", f"Done - {filename} ({format_indian_number(len(xlsx_bytes) // 1024)} KB)")
    if progress_cb:
        progress_cb(1.0, "Report ready")

    return {
        "output_path": str(output_path),
        "download_filename": filename,
        "missing_codes": sorted(missing_codes, key=lambda c: (len(c), c)),
        "row_count": len(df),
        "raw_row_count": raw_row_count,
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "log": log,
    }


@router.post("/process")
def submit_process(body: ProcessRequest, user: User = Depends(get_current_user)):
    with _stash_lock:
        _prune_stash()
        entry = _stashed.get(body.token)
        if entry is not None and entry["owner_id"] != user.id:
            entry = None
    if entry is None:
        raise HTTPException(status_code=404, detail="Upload token not found or expired — please re-upload the file")

    selected = set(body.account_codes or [])
    pairs = processor.distinct_account_descriptions(entry["columns"], entry["rows"])
    pivot_accounts = [(code, desc) for code, desc in pairs if code in selected]

    input_path = entry["path"]
    output_path = SCRATCH_DIR / f"{Path(input_path).stem}_Location_Report.xlsx"

    job_id = submit_job(
        _run_process_job,
        input_path,
        entry["columns"],
        entry["rows"],
        pivot_accounts,
        str(output_path),
        owner_id=user.id,
    )

    # Deliberately NOT popped here: the desktop app's "Mapping Not Found"
    # flow re-runs the exact same parsed file against freshly-reloaded
    # mappings when the user clicks "Regenerate Report" after fixing a
    # mapping, without re-uploading. Keeping the token alive (until its TTL
    # or the next /accounts call for a new file) lets the frontend call
    # /process again with the same token instead of forcing a re-upload.
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

    filename = result.get("download_filename") or "Location_Report.xlsx"
    return FileResponse(path=str(output_path), filename=filename, media_type=_XLSX_MEDIA_TYPE)


# ── missing-mapping fix-up (mirrors _MissingPopup's "Update Mapping" dialog) ─

@router.post("/mappings/fix")
def fix_mapping(payload: MappingFixRequest, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Sets Location Name (Location Code Map) and Region (Location Region
    Map) together for one Location Code — exactly like the desktop app's
    _MissingPopup._resolve_selected, which required both fields non-empty.
    Accounts Incharge is deliberately not settable here: it always derives
    from the Region Incharge Map at report-generation time."""
    code = payload.location_code.strip()
    name = payload.location_name.strip()
    region = payload.region.strip()
    if not code or not name or not region:
        raise HTTPException(status_code=400, detail="location_code, location_name and region are all required")

    mapping_store.upsert_location_code(db, code, name)
    mapping_store.upsert_location_region(db, name, region)
    return {"ok": True, "location_code": code, "location_name": name, "region": region}


# ── 1. Location Code Map  (key = location_code) ─────────────────────────────

@router.get("/mappings/location-codes")
def list_location_codes(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    loc_name_map, *_ = mapping_store.load_all(db)
    return [
        {"location_code": code, "location_name": name}
        for code, name in sorted(loc_name_map.items(), key=lambda kv: (len(kv[0]), kv[0]))
    ]


@router.post("/mappings/location-codes")
def add_location_code(body: LocationCodeBody, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    code = body.location_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="location_code is required")
    name = body.location_name.strip()
    mapping_store.upsert_location_code(db, code, name)
    return {"ok": True, "location_code": code, "location_name": name}


@router.put("/mappings/location-codes/{code}")
def edit_location_code(code: str, body: LocationCodeBody, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    new_code = body.location_code.strip()
    name = body.location_name.strip()

    if new_code != code:
        if not mapping_store.delete_location_code(db, code):
            raise HTTPException(status_code=404, detail="Location code not found")
    mapping_store.upsert_location_code(db, new_code, name)
    return {"ok": True, "location_code": new_code, "location_name": name}


@router.delete("/mappings/location-codes/{code}")
def delete_location_code(code: str, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    if not mapping_store.delete_location_code(db, code):
        raise HTTPException(status_code=404, detail="Location code not found")
    return {"ok": True}


# ── 2. Location Region Map  (key = location_name) ───────────────────────────

@router.get("/mappings/location-region")
def list_location_region(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, _, name_region_map, _ = mapping_store.load_all(db)
    return [
        {"location_name": name, "region": region}
        for name, region in sorted(name_region_map.items(), key=lambda kv: kv[0].lower())
    ]


@router.post("/mappings/location-region")
def add_location_region(body: LocationRegionBody, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    name = body.location_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="location_name is required")
    region = body.region.strip()
    mapping_store.upsert_location_region(db, name, region)
    return {"ok": True, "location_name": name, "region": region}


@router.put("/mappings/location-region/{location_name}")
def edit_location_region(location_name: str, body: LocationRegionBody, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    new_name = body.location_name.strip()
    region = body.region.strip()

    if new_name != location_name:
        if not mapping_store.delete_location_region(db, location_name):
            raise HTTPException(status_code=404, detail="Location name not found")
    mapping_store.upsert_location_region(db, new_name, region)
    return {"ok": True, "location_name": new_name, "region": region}


@router.delete("/mappings/location-region/{location_name}")
def delete_location_region(location_name: str, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    if not mapping_store.delete_location_region(db, location_name):
        raise HTTPException(status_code=404, detail="Location name not found")
    return {"ok": True}


# ── 3. Region Incharge Map  (key = region) ──────────────────────────────────

@router.get("/mappings/region-incharge")
def list_region_incharge(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, region_incharge_map, _, _ = mapping_store.load_all(db)
    return [
        {"region": region, "accounts_incharge": incharge}
        for region, incharge in sorted(region_incharge_map.items(), key=lambda kv: kv[0].lower())
    ]


@router.post("/mappings/region-incharge")
def add_region_incharge(body: RegionInchargeBody, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    region = body.region.strip()
    if not region:
        raise HTTPException(status_code=400, detail="region is required")
    incharge = body.accounts_incharge.strip()
    mapping_store.upsert_region_incharge(db, region, incharge)
    return {"ok": True, "region": region, "accounts_incharge": incharge}


@router.put("/mappings/region-incharge/{region}")
def edit_region_incharge(region: str, body: RegionInchargeBody, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    new_region = body.region.strip()
    incharge = body.accounts_incharge.strip()

    if new_region != region:
        if not mapping_store.delete_region_incharge(db, region):
            raise HTTPException(status_code=404, detail="Region not found")
    mapping_store.upsert_region_incharge(db, new_region, incharge)
    return {"ok": True, "region": new_region, "accounts_incharge": incharge}


@router.delete("/mappings/region-incharge/{region}")
def delete_region_incharge(region: str, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    if not mapping_store.delete_region_incharge(db, region):
        raise HTTPException(status_code=404, detail="Region not found")
    return {"ok": True}


# ── 4. Account HO Map  (key = account_code) ─────────────────────────────────

@router.get("/mappings/account-ho")
def list_account_ho(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, _, _, account_ho_map = mapping_store.load_all(db)
    return [
        {"account_code": code, "ho_person": person}
        for code, person in sorted(account_ho_map.items(), key=lambda kv: (len(kv[0]), kv[0]))
    ]


@router.post("/mappings/account-ho")
def add_account_ho(body: AccountHoBody, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    code = body.account_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="account_code is required")
    person = body.ho_person.strip()
    mapping_store.upsert_account_ho(db, code, person)
    return {"ok": True, "account_code": code, "ho_person": person}


@router.put("/mappings/account-ho/{account_code}")
def edit_account_ho(account_code: str, body: AccountHoBody, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    new_code = body.account_code.strip()
    person = body.ho_person.strip()

    if new_code != account_code:
        if not mapping_store.delete_account_ho(db, account_code):
            raise HTTPException(status_code=404, detail="Account code not found")
    mapping_store.upsert_account_ho(db, new_code, person)
    return {"ok": True, "account_code": new_code, "ho_person": person}


@router.delete("/mappings/account-ho/{account_code}")
def delete_account_ho(account_code: str, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    if not mapping_store.delete_account_ho(db, account_code):
        raise HTTPException(status_code=404, detail="Account code not found")
    return {"ok": True}
