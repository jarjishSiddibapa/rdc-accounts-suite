"""RDC Payables Aging Report tool routes.

Ports the desktop app's flow (see the sibling source repo's main.py /
processor.py / mapping_store.py) onto the shared FastAPI backend:

  1. POST /process          – upload an Oracle ERP HTML/XLS export + a GL
                               cutoff month/year, run the parse -> transform
                               -> pivot pipeline as a background job.
  2. GET  /jobs/{job_id}     – poll job status/progress.
  3. GET  /download/{job_id} – download the finished .xlsx once done.
  4. POST /mappings/fix      – the REST equivalent of the desktop app's
                               "Mapping Not Found" popup's "Update Mapping"
                               dialog: set Region (+ optionally Accounts
                               Incharge) for one Vendor Site Code.
  5. Six CRUD route groups, one per mapping table (vendor-site-codes,
     location-codes, row-exclusions, invoice-overrides, region-incharge,
     transaction-type-overrides) — all backed by the shared MySQL database
     (see models.py), not an xlsx file.
"""

from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
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
from app.services.rdc_payables import mapping_store, processor
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/rdc-payables", tags=["rdc-payables"],
    dependencies=[Depends(require_app_access("rdc-payables"))],
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Uploaded report files are bounded centrally by app.uploads.save_upload.
# ── composite-key helpers (Row Exclusions / Invoice Overrides / Transaction
#    Type Overrides are all keyed by a (Vendor Number, Invoice Number) pair;
#    encoded in URLs the same way the desktop app encodes its Treeview iids,
#    "VENDOR|INVOICE" — Invoice Number may be empty for a vendor-wide entry,
#    e.g. "V123|". Clients should percent-encode the "|" when building the
#    path segment (e.g. encodeURIComponent in JS turns it into %7C, which
#    FastAPI/Starlette decode back to "|" automatically). ──────────────────

def _decode_pair_key(key: str) -> tuple[str, str]:
    vn, _, inv = key.partition("|")
    return vn, inv


# ── request bodies ─────────────────────────────────────────────────────────

class MappingFixRequest(BaseModel):
    vendor_site_code: str
    region: str
    accounts_incharge: Optional[str] = None


class VendorSiteCodeBody(BaseModel):
    vendor_site_code: str
    region: str
    accounts_incharge: Optional[str] = None


class LocationCodeBody(BaseModel):
    location_code: str
    region: str
    accounts_incharge: Optional[str] = None


class RowExclusionBody(BaseModel):
    vendor_number: str
    invoice_number: Optional[str] = ""


class InvoiceOverrideBody(BaseModel):
    vendor_number: str
    invoice_number: Optional[str] = ""
    region: str
    accounts_incharge: Optional[str] = None


class RegionInchargeBody(BaseModel):
    region: str
    accounts_incharge: str


class TxnTypeOverrideBody(BaseModel):
    vendor_number: str
    invoice_number: Optional[str] = ""
    transaction_type: str


# ── /process job pipeline ──────────────────────────────────────────────────

def _run_process_job(input_path: str, output_path: str,
                      cutoff_year: int, cutoff_month: int,
                      progress_cb=None) -> dict:
    """Runs in the background job pool (a plain thread, not tied to any
    request) — mirrors the desktop app's _worker() in main.py and Flask
    app.py's /process route, including how the "Mapping Not Found" /
    missing-codes list is computed
    (df.loc[df["Region"] == "", "Vendor Site Code"]).

    Opens its own short-lived DB session via SessionLocal() rather than
    reusing a FastAPI-injected one: by the time this function actually
    runs, the request that queued the job has already returned and its
    Depends(get_db) session has been closed."""
    log: list[str] = []

    def add_log(level: str, message: str) -> None:
        stamp = now_ist().strftime("%H:%M:%S")
        log.append(f"[{level}] [{stamp}] {message}")

    if progress_cb:
        progress_cb(0.03, "Loading centralized mappings...")
    add_log("INFO", f"Starting: {Path(input_path).name}")

    db = SessionLocal()
    try:
        (mapping, loc_code_map, exclusions, invoice_overrides,
         region_incharge_map, txn_type_overrides) = mapping_store.load_all(db)
    finally:
        db.close()

    if progress_cb:
        progress_cb(0.08, "Parsing HTML report...")
    parse_started = perf_counter()
    columns, rows = processor.parse_html_report(input_path)
    raw_row_count = len(rows)
    parse_seconds = perf_counter() - parse_started
    add_log("OK", f"Parsed {format_indian_number(raw_row_count)} rows in {parse_seconds:.1f}s")

    if progress_cb:
        progress_cb(0.40, "Applying filters and mappings...")
    cutoff_label = datetime(cutoff_year, cutoff_month, 1).strftime("%b-%Y")
    add_log("INFO", f"Including GL data through: {cutoff_label}")
    df, reporting_ref_date = processor.process_report(
        columns, rows, cutoff_year, cutoff_month, mapping,
        loc_code_map=loc_code_map,
        exclusions=exclusions,
        invoice_overrides=invoice_overrides,
        region_incharge_map=region_incharge_map,
        txn_type_overrides=txn_type_overrides,
    )

    add_log("OK", f"Output rows: {format_indian_number(len(df))}")
    if reporting_ref_date:
        add_log(
            "INFO",
            "Reporting month (for aging): "
            f"{reporting_ref_date.strftime('%b-%Y')} "
            f"(ref date {reporting_ref_date.strftime('%d-%b-%Y')})",
        )

    if progress_cb:
        progress_cb(0.75, "Checking mappings and report totals...")
    missing_codes: list = []
    if "Region" in df.columns and "Vendor Site Code" in df.columns:
        unmapped_df = df[df["Region"] == ""]
        if not unmapped_df.empty:
            missing_codes = unmapped_df["Vendor Site Code"].dropna().unique().tolist()
    if missing_codes:
        add_log("WARN", f"Unmapped site codes: {format_indian_number(len(missing_codes))}")

    matched_count = int((df["Region"] != "").sum()) if "Region" in df.columns else 0
    unmatched_count = len(df) - matched_count
    transaction_type_counts = (
        {str(key): int(value) for key, value in df["Transaction Type"].value_counts().items()}
        if "Transaction Type" in df.columns else {}
    )
    aging_bucket_counts = (
        {str(key): int(value) for key, value in df["Aging Bucket"].value_counts().items()}
        if "Aging Bucket" in df.columns else {}
    )
    for transaction_type, count in transaction_type_counts.items():
        add_log("INFO", f"  {transaction_type}: {format_indian_number(count)}")
    for bucket, count in aging_bucket_counts.items():
        add_log("INFO", f"  Aging - {bucket}: {format_indian_number(count)}")

    if progress_cb:
        progress_cb(0.86, "Generating Excel output...")
    add_log("INFO", "Generating Excel output...")
    xlsx_bytes = processor.to_excel_bytes(df, missing_codes if missing_codes else None)
    Path(output_path).write_bytes(xlsx_bytes)

    try:
        Path(input_path).unlink(missing_ok=True)
    except OSError:
        pass

    filename = f"Payables_Report_Through_{cutoff_label}.xlsx"
    add_log("OK", f"Done - {filename} ({format_indian_number(len(xlsx_bytes) // 1024)} KB)")
    if progress_cb:
        progress_cb(1.0, "Report ready")
    return {
        "output_path": str(output_path),
        "download_filename": filename,
        "unmapped_vendor_sites": missing_codes,
        "row_count": len(df),
        "raw_row_count": raw_row_count,
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "transaction_type_counts": transaction_type_counts,
        "aging_bucket_counts": aging_bucket_counts,
        "log": log,
        "reporting_ref_date": reporting_ref_date.strftime("%Y-%m-%d") if reporting_ref_date else None,
    }


@router.post("/process")
async def submit_process(
    file: UploadFile = File(...),
    cutoff_year: int = Form(...),
    cutoff_month: int = Form(...),
    user: User = Depends(get_current_user),
):
    """Real signature behind this: processor.parse_html_report(file_path)
    and processor.process_report(columns, data_rows, cutoff_year,
    cutoff_month, mapping, loc_code_map=, exclusions=, invoice_overrides=,
    region_incharge_map=, txn_type_overrides=) — cutoff_year/cutoff_month
    are the two form fields the frontend must supply (int GL year, int GL
    month 1-12); the mapping tables are loaded server-side from MySQL, not
    supplied by the client."""
    if not (1 <= cutoff_month <= 12):
        raise HTTPException(status_code=400, detail="cutoff_month must be between 1 and 12")

    input_path = await save_upload(file, SCRATCH_DIR)
    output_path = SCRATCH_DIR / f"{input_path.stem}_Payables_Report.xlsx"

    job_id = submit_job(
        _run_process_job,
        str(input_path),
        str(output_path),
        cutoff_year,
        cutoff_month,
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

    filename = result.get("download_filename") or "Payables_Report.xlsx"
    return FileResponse(path=str(output_path), filename=filename, media_type=_XLSX_MEDIA_TYPE)


# ── missing-mapping fix-up (mirrors _MissingPopup's "Update Mapping" dialog) ─

@router.post("/mappings/fix")
def fix_mapping(payload: MappingFixRequest, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    site = payload.vendor_site_code.strip()
    region = payload.region.strip()
    if not site or not region:
        raise HTTPException(status_code=400, detail="vendor_site_code and region are required")

    # Same rule as _MissingPopup._resolve_selected in main.py: Accounts
    # Incharge is derived from the Region Incharge Map, not entered
    # directly, unless the caller explicitly supplies one.
    incharge = (payload.accounts_incharge or "").strip() or mapping_store.get_region_incharge(db, region)
    mapping_store.upsert_vendor_site_code(db, site, region, incharge)
    return {"ok": True, "vendor_site_code": site, "region": region, "accounts_incharge": incharge}


# ── 1. Vendor Site Codes  (key = vendor_site_code, case-insensitive) ────────

@router.get("/vendor-site-codes")
def list_vendor_site_codes(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    mapping, *_ = mapping_store.load_all(db)
    return [
        {"vendor_site_code": e.get("_key", k), "region": e.get("Region", ""),
         "accounts_incharge": e.get("Accounts Incharge", "")}
        for k, e in sorted(mapping.items())
    ]


@router.post("/vendor-site-codes")
def add_vendor_site_code(body: VendorSiteCodeBody, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    site = body.vendor_site_code.strip()
    region = body.region.strip()
    if not site:
        raise HTTPException(status_code=400, detail="vendor_site_code is required")
    incharge = (body.accounts_incharge or "").strip() or mapping_store.get_region_incharge(db, region)
    mapping_store.upsert_vendor_site_code(db, site, region, incharge)
    return {"ok": True, "vendor_site_code": site, "region": region, "accounts_incharge": incharge}


@router.put("/vendor-site-codes/{key}")
def edit_vendor_site_code(key: str, body: VendorSiteCodeBody, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    new_site = body.vendor_site_code.strip()
    region = body.region.strip()
    incharge = (body.accounts_incharge or "").strip() or mapping_store.get_region_incharge(db, region)

    if new_site.upper() != key.strip().upper():
        if not mapping_store.delete_vendor_site_code(db, key):
            raise HTTPException(status_code=404, detail="Vendor site code not found")
    mapping_store.upsert_vendor_site_code(db, new_site, region, incharge)
    return {"ok": True, "vendor_site_code": new_site, "region": region, "accounts_incharge": incharge}


@router.delete("/vendor-site-codes/{key}")
def delete_vendor_site_code(key: str, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    if not mapping_store.delete_vendor_site_code(db, key):
        raise HTTPException(status_code=404, detail="Vendor site code not found")
    return {"ok": True}


# ── 2. Location Code Map  (key = location_code, case-sensitive as stored) ───

@router.get("/location-codes")
def list_location_codes(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, loc_code_map, *_ = mapping_store.load_all(db)
    return [
        {"location_code": code, "region": e.get("Region", ""),
         "accounts_incharge": e.get("Accounts Incharge", "")}
        for code, e in sorted(loc_code_map.items())
    ]


@router.post("/location-codes")
def add_location_code(body: LocationCodeBody, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    code = body.location_code.strip()
    region = body.region.strip()
    if not code:
        raise HTTPException(status_code=400, detail="location_code is required")
    incharge = (body.accounts_incharge or "").strip() or mapping_store.get_region_incharge(db, region)
    mapping_store.upsert_location_code(db, code, region, incharge)
    return {"ok": True, "location_code": code, "region": region, "accounts_incharge": incharge}


@router.put("/location-codes/{key}")
def edit_location_code(key: str, body: LocationCodeBody, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    new_code = body.location_code.strip()
    region = body.region.strip()
    incharge = (body.accounts_incharge or "").strip() or mapping_store.get_region_incharge(db, region)

    if new_code != key:
        if not mapping_store.delete_location_code(db, key):
            raise HTTPException(status_code=404, detail="Location code not found")
    mapping_store.upsert_location_code(db, new_code, region, incharge)
    return {"ok": True, "location_code": new_code, "region": region, "accounts_incharge": incharge}


@router.delete("/location-codes/{key}")
def delete_location_code(key: str, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    if not mapping_store.delete_location_code(db, key):
        raise HTTPException(status_code=404, detail="Location code not found")
    return {"ok": True}


# ── 3. Row Exclusions  (key = "VENDOR_NUMBER|INVOICE_NUMBER", both upper) ───

@router.get("/row-exclusions")
def list_row_exclusions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, _, exclusions, *_ = mapping_store.load_all(db)
    return [{"vendor_number": vn, "invoice_number": inv} for vn, inv in sorted(exclusions)]


@router.post("/row-exclusions")
def add_row_exclusion(body: RowExclusionBody, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    vn = body.vendor_number.strip().upper()
    inv = (body.invoice_number or "").strip().upper()
    if not vn:
        raise HTTPException(status_code=400, detail="vendor_number is required")
    mapping_store.upsert_row_exclusion(db, vn, inv)
    return {"ok": True, "vendor_number": vn, "invoice_number": inv}


@router.put("/row-exclusions/{key}")
def edit_row_exclusion(key: str, body: RowExclusionBody, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    old_vn, old_inv = _decode_pair_key(key)
    old_vn, old_inv = old_vn.upper(), old_inv.upper()

    new_vn = body.vendor_number.strip().upper()
    new_inv = (body.invoice_number or "").strip().upper()

    if (new_vn, new_inv) != (old_vn, old_inv):
        if not mapping_store.delete_row_exclusion(db, old_vn, old_inv):
            raise HTTPException(status_code=404, detail="Row exclusion not found")
    mapping_store.upsert_row_exclusion(db, new_vn, new_inv)
    return {"ok": True, "vendor_number": new_vn, "invoice_number": new_inv}


@router.delete("/row-exclusions/{key}")
def delete_row_exclusion(key: str, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    vn, inv = _decode_pair_key(key)
    if not mapping_store.delete_row_exclusion(db, vn.upper(), inv.upper()):
        raise HTTPException(status_code=404, detail="Row exclusion not found")
    return {"ok": True}


# ── 4. Invoice Overrides  (key = "VENDOR_NUMBER|INVOICE_NUMBER", both upper) ─

@router.get("/invoice-overrides")
def list_invoice_overrides(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, _, _, invoice_overrides, *_ = mapping_store.load_all(db)
    return [
        {"vendor_number": vn, "invoice_number": inv,
         "region": e.get("Region", ""), "accounts_incharge": e.get("Accounts Incharge", "")}
        for (vn, inv), e in sorted(invoice_overrides.items())
    ]


@router.post("/invoice-overrides")
def add_invoice_override(body: InvoiceOverrideBody, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    vn = body.vendor_number.strip().upper()
    inv = (body.invoice_number or "").strip().upper()
    region = body.region.strip()
    if not vn:
        raise HTTPException(status_code=400, detail="vendor_number is required")
    incharge = (body.accounts_incharge or "").strip() or mapping_store.get_region_incharge(db, region)
    mapping_store.upsert_invoice_override(db, vn, inv, region, incharge)
    return {"ok": True, "vendor_number": vn, "invoice_number": inv,
            "region": region, "accounts_incharge": incharge}


@router.put("/invoice-overrides/{key}")
def edit_invoice_override(key: str, body: InvoiceOverrideBody, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    old_vn, old_inv = _decode_pair_key(key)
    old_vn, old_inv = old_vn.upper(), old_inv.upper()

    new_vn = body.vendor_number.strip().upper()
    new_inv = (body.invoice_number or "").strip().upper()
    region = body.region.strip()
    incharge = (body.accounts_incharge or "").strip() or mapping_store.get_region_incharge(db, region)

    if (new_vn, new_inv) != (old_vn, old_inv):
        if not mapping_store.delete_invoice_override(db, old_vn, old_inv):
            raise HTTPException(status_code=404, detail="Invoice override not found")
    mapping_store.upsert_invoice_override(db, new_vn, new_inv, region, incharge)
    return {"ok": True, "vendor_number": new_vn, "invoice_number": new_inv,
            "region": region, "accounts_incharge": incharge}


@router.delete("/invoice-overrides/{key}")
def delete_invoice_override(key: str, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    vn, inv = _decode_pair_key(key)
    if not mapping_store.delete_invoice_override(db, vn.upper(), inv.upper()):
        raise HTTPException(status_code=404, detail="Invoice override not found")
    return {"ok": True}


# ── 5. Region Incharge Map  (key = region) ──────────────────────────────────

@router.get("/region-incharge")
def list_region_incharge(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    *_, region_incharge_map, _ = mapping_store.load_all(db)
    return [
        {"region": region, "accounts_incharge": incharge}
        for region, incharge in sorted(region_incharge_map.items(), key=lambda kv: kv[0].lower())
    ]


@router.post("/region-incharge")
def add_region_incharge(body: RegionInchargeBody, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    region = body.region.strip()
    incharge = body.accounts_incharge.strip()
    if not region:
        raise HTTPException(status_code=400, detail="region is required")
    mapping_store.upsert_region_incharge(db, region, incharge)
    return {"ok": True, "region": region, "accounts_incharge": incharge}


@router.put("/region-incharge/{key}")
def edit_region_incharge(key: str, body: RegionInchargeBody, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    new_region = body.region.strip()
    incharge = body.accounts_incharge.strip()

    if new_region != key:
        if not mapping_store.delete_region_incharge(db, key):
            raise HTTPException(status_code=404, detail="Region not found")
    mapping_store.upsert_region_incharge(db, new_region, incharge)
    return {"ok": True, "region": new_region, "accounts_incharge": incharge}


@router.delete("/region-incharge/{key}")
def delete_region_incharge(key: str, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    if not mapping_store.delete_region_incharge(db, key):
        raise HTTPException(status_code=404, detail="Region not found")
    return {"ok": True}


# ── 6. Transaction Type Overrides  (key = "VENDOR_NUMBER|INVOICE_NUMBER") ───

@router.get("/transaction-type-overrides")
def list_txn_type_overrides(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    *_, txn_type_overrides = mapping_store.load_all(db)
    return [
        {"vendor_number": vn, "invoice_number": inv, "transaction_type": tt}
        for (vn, inv), tt in sorted(txn_type_overrides.items())
    ]


@router.post("/transaction-type-overrides")
def add_txn_type_override(body: TxnTypeOverrideBody, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    vn = body.vendor_number.strip().upper()
    inv = (body.invoice_number or "").strip().upper()
    tt = body.transaction_type.strip()
    if not vn:
        raise HTTPException(status_code=400, detail="vendor_number is required")
    if not tt:
        raise HTTPException(status_code=400, detail="transaction_type cannot be empty")
    mapping_store.upsert_txn_type_override(db, vn, inv, tt)
    return {"ok": True, "vendor_number": vn, "invoice_number": inv, "transaction_type": tt}


@router.put("/transaction-type-overrides/{key}")
def edit_txn_type_override(key: str, body: TxnTypeOverrideBody, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    old_vn, old_inv = _decode_pair_key(key)
    old_vn, old_inv = old_vn.upper(), old_inv.upper()

    new_vn = body.vendor_number.strip().upper()
    new_inv = (body.invoice_number or "").strip().upper()
    tt = body.transaction_type.strip()
    if not tt:
        raise HTTPException(status_code=400, detail="transaction_type cannot be empty")

    if (new_vn, new_inv) != (old_vn, old_inv):
        if not mapping_store.delete_txn_type_override(db, old_vn, old_inv):
            raise HTTPException(status_code=404, detail="Transaction type override not found")
    mapping_store.upsert_txn_type_override(db, new_vn, new_inv, tt)
    return {"ok": True, "vendor_number": new_vn, "invoice_number": new_inv, "transaction_type": tt}


@router.delete("/transaction-type-overrides/{key}")
def delete_txn_type_override(key: str, db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    vn, inv = _decode_pair_key(key)
    if not mapping_store.delete_txn_type_override(db, vn.upper(), inv.upper()):
        raise HTTPException(status_code=404, detail="Transaction type override not found")
    return {"ok": True}
