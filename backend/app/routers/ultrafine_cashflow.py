"""Ultrafine Cashflow Generator routes.

  * POST /statements            upload one or more bank statements (job): the
                                lines are stored once (overlaps are ignored)
                                and every month that received new lines is
                                regenerated when all of its lines are mapped.
  * GET  /statements            upload history.
  * GET  /months                months with data, issues and the stored file.
  * POST /months/{m}/generate   (re)generate one month (job).
  * GET  /months/{m}/download   the latest stored workbook of the month.
  * GET  /months/{m}/transactions, PUT /transactions/{id}/override
                                review lines; set a party or type a split.
  * GET/PUT /months/{m}/setup   opening-balance / statutory inputs and the
                                planning block of the month.
  * /mappings/...               CRUD for the normalized master data.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.database import SessionLocal, get_db
from app.jobs import JobUserError, cancel_job, get_job, submit_job
from app.models import User
from app.permissions import require_app_access
from app.services.ultrafine_cashflow import mapping_store as store
from app.services.ultrafine_cashflow.generate import generate_month
from app.services.ultrafine_cashflow.ingest import ingest_statement
from app.services.ultrafine_cashflow.keys import clean_name, description_key
from app.services.ultrafine_cashflow.models import (
    BankAccount,
    CashflowFile,
    CfLocation,
    Location,
    MonthInput,
    Party,
    PlanItem,
    PlanSection,
    StatementUpload,
    Transaction,
    TransactionSplit,
)
from app.services.ultrafine_cashflow.resolver import (
    allocate,
    allocations_for_month,
    load_maps,
    load_splits,
    transactions_in_month,
    unmapped_summary,
)
from app.services.ultrafine_cashflow.statement_reader import read_statement
from app.services.ultrafine_cashflow.summary import month_figures, months_with_data, plan_blocks
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/ultrafine-cashflow", tags=["ultrafine-cashflow"],
    dependencies=[Depends(require_app_access("ultrafine-cashflow"))],
)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month(value: str) -> str:
    if not _MONTH_RE.match(value):
        raise HTTPException(status_code=400, detail="Month must look like 2026-09.")
    return value


def _mapping_call(fn, *args, **kwargs):
    """Run a mapping_store call, turning user-correctable errors into 400s."""
    try:
        return fn(*args, **kwargs)
    except store.MappingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _commit(db: Session, fn, *args, **kwargs):
    result = _mapping_call(fn, db, *args, **kwargs)
    db.commit()
    return result


# ── jobs ────────────────────────────────────────────────────────────────────

def _job_ingest(paths: list[str], names: list[str], user_id: int,
                progress_cb=None, cancel_event=None) -> dict:
    """Store every statement, then regenerate the months that got new lines."""
    files: list[dict] = []
    touched: dict[str, dict] = {}
    db = SessionLocal()
    try:
        for index, (path, name) in enumerate(zip(paths, names)):
            if progress_cb:
                progress_cb(0.05 + 0.55 * index / max(len(paths), 1), f"Reading {name}...")
            try:
                statement = read_statement(path)
                outcome = ingest_statement(db, statement, name, user_id)
                db.commit()
                files.append({"file": name, "ok": True, **outcome})
                for month, counts in outcome["months"].items():
                    entry = touched.setdefault(month, {"new": 0, "duplicate": 0})
                    entry["new"] += counts["new"]
                    entry["duplicate"] += counts["duplicate"]
            except JobUserError as exc:
                db.rollback()
                files.append({"file": name, "ok": False, "error": str(exc)})
            finally:
                Path(path).unlink(missing_ok=True)

        months: list[dict] = []
        changed = [month for month, counts in touched.items() if counts["new"] > 0]
        for index, month in enumerate(sorted(touched)):
            if progress_cb:
                progress_cb(0.65 + 0.3 * index / max(len(touched), 1), f"Preparing {month}...")
            if month in changed:
                result = generate_month(db, month, user_id)
                db.commit()
            else:
                result = {"month": month, "status": "unchanged"}
            months.append({**result, **touched[month]})
        return {"files": files, "months": months}
    finally:
        db.close()


def _job_generate(month: str, user_id: int, allow_unmapped: bool = False,
                  progress_cb=None, cancel_event=None) -> dict:
    db = SessionLocal()
    try:
        if progress_cb:
            progress_cb(0.2, f"Building the {month} cashflow...")
        result = generate_month(db, month, user_id, allow_unmapped=allow_unmapped)
        db.commit()
        return result
    finally:
        db.close()


@router.post("/statements")
async def upload_statements(files: list[UploadFile] = File(...), user: User = Depends(get_current_user)):
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one statement.")
    paths: list[str] = []
    names: list[str] = []
    for upload in files:
        paths.append(str(await save_upload(upload, SCRATCH_DIR)))
        names.append(Path(upload.filename or "statement").name)
    return {"job_id": submit_job(_job_ingest, paths, names, user.id, owner_id=user.id)}


@router.post("/months/{month}/generate")
def generate(month: str, allow_unmapped: bool = False, user: User = Depends(get_current_user)):
    """`allow_unmapped=true` writes the workbook even though some lines are not
    mapped yet; those lines are highlighted and flagged inside it."""
    return {"job_id": submit_job(_job_generate, _month(month), user.id,
                                 allow_unmapped=allow_unmapped, owner_id=user.id)}


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


# ── statements and months ───────────────────────────────────────────────────

@router.get("/statements")
def list_statements(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    accounts = {a.id: a.account_no for a in db.query(BankAccount)}
    rows = (
        db.query(StatementUpload).filter(StatementUpload.is_deleted == False)  # noqa: E712
        .order_by(StatementUpload.uploaded_at.desc()).limit(200).all()
    )
    stored = dict(
        db.query(Transaction.upload_id, func.count(Transaction.id))
        .filter(Transaction.is_deleted == False)  # noqa: E712
        .group_by(Transaction.upload_id).all()
    )
    return [
        {"id": r.id, "account_no": accounts.get(r.account_id, ""), "filename": r.filename,
         "uploaded_at": r.uploaded_at.isoformat(timespec="seconds"),
         "period_from": r.period_from.isoformat() if r.period_from else None,
         "period_to": r.period_to.isoformat() if r.period_to else None,
         "line_count": r.line_count, "new_count": r.new_count, "duplicate_count": r.duplicate_count,
         "stored_count": int(stored.get(r.id, 0))}
        for r in rows
    ]


@router.delete("/statements/{upload_id}")
def remove_statement(upload_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Take back an upload (wrong account, wrong file): the lines THIS file added are archived, never
    deleted, so uploading the file again brings them back. Lines an earlier statement already held
    stay. Months that lose lines should be regenerated."""
    upload = db.get(StatementUpload, upload_id)
    if upload is None or upload.is_deleted:
        raise HTTPException(status_code=404, detail="Statement not found")
    lines = db.query(Transaction).filter(Transaction.upload_id == upload.id,
                                         Transaction.is_deleted == False).all()  # noqa: E712
    months = sorted({line.txn_dt.strftime("%Y-%m") for line in lines})
    for line in lines:
        line.is_deleted = True
    upload.is_deleted = True
    db.commit()
    return {"removed_lines": len(lines), "months": months}


@router.get("/months")
def list_months(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    maps = load_maps(db)
    files = {f.month: f for f in db.query(CashflowFile).filter(CashflowFile.is_deleted == False)}  # noqa: E712
    result = []
    for month in sorted(set(months_with_data(db)) | set(files), reverse=True):
        allocations = allocations_for_month(db, month, maps)
        stored = files.get(month)
        result.append({
            "month": month,
            "transactions": len({a.txn_id for a in allocations}),
            "payments": float(sum((a.amount for a in allocations if a.direction == "D"), Decimal(0))),
            "receipts": float(sum((a.amount for a in allocations if a.direction == "C"), Decimal(0))),
            "issue_count": sum(1 for a in allocations if a.issues),
            "file": None if stored is None else {
                "name": stored.file_name, "version": stored.version, "source": stored.source or "generated",
                "generated_at": stored.generated_at.isoformat(timespec="seconds"),
                "available": Path(stored.file_path).is_file(),
            },
        })
    return result


@router.get("/months/{month}/download")
def download_month(month: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stored = db.query(CashflowFile).filter(CashflowFile.month == _month(month),
                                           CashflowFile.is_deleted == False).first()  # noqa: E712
    if stored is None or not Path(stored.file_path).is_file():
        raise HTTPException(status_code=404, detail="No cashflow has been generated for this month yet.")
    return FileResponse(path=stored.file_path, filename=stored.file_name, media_type=_XLSX)


@router.get("/months/{month}/unmapped")
def month_unmapped(month: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    maps = load_maps(db)
    return unmapped_summary(allocations_for_month(db, _month(month), maps), maps)


# ── transactions ────────────────────────────────────────────────────────────

@router.get("/months/{month}/transactions")
def month_transactions(
    month: str, direction: Optional[str] = None, status: str = "all", q: str = "",
    page: int = 1, page_size: int = 50,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    maps = load_maps(db)
    txns = transactions_in_month(db, _month(month))
    splits = load_splits(db, [t.id for t in txns])
    needle = q.strip().casefold()
    items = []
    for txn in txns:
        if direction in ("D", "C") and txn.direction != direction:
            continue
        rows = allocate(txn, maps, splits.get(txn.id, []))
        issues = sorted({i for r in rows for i in r.issues})
        manual = bool(splits.get(txn.id)) or txn.party_id is not None
        if status == "issues" and not issues:
            continue
        if status == "manual" and not manual:
            continue
        name = rows[0].name if rows else ""
        if needle and needle not in txn.description.casefold() and needle not in name.casefold():
            continue
        items.append({
            "id": txn.id, "txn_dt": txn.txn_dt.isoformat(timespec="seconds"), "direction": txn.direction,
            "description": txn.description, "reference_no": txn.reference_no or "",
            "amount": float(txn.amount), "name": name, "issues": issues, "manual": manual,
            "party_override": txn.party_id is not None,
            "desc_key": description_key(txn.description),
            "allocations": [{"location": r.location, "cf_location": r.cf_location,
                             "amount": float(r.amount)} for r in rows],
            "splits": [{"location": maps.locations[s.location_id].name if s.location_id in maps.locations else "",
                        "cf_location": maps.cf_locations[s.cf_location_id].name
                        if s.cf_location_id in maps.cf_locations else "",
                        "amount": float(s.amount)} for s in splits.get(txn.id, [])],
        })
    page_size = max(1, min(page_size, 200))
    start = (max(page, 1) - 1) * page_size
    return {"total": len(items), "page": page, "page_size": page_size, "items": items[start:start + page_size]}


class SplitIn(BaseModel):
    location: str
    cf_location: Optional[str] = None
    amount: float


class OverrideIn(BaseModel):
    party: Optional[str] = None
    party_location: Optional[str] = None
    party_cf_location: Optional[str] = None
    splits: list[SplitIn] = []


@router.put("/transactions/{txn_id}/override")
def set_override(txn_id: int, body: OverrideIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    txn = db.get(Transaction, txn_id)
    if txn is None or txn.is_deleted:
        raise HTTPException(status_code=404, detail="Transaction not found")

    def apply():
        # A new name (or one still without a place) needs its location and CF location
        # together, like the fix panel; a half-placed name would only come back as
        # "no CF location".
        txn.party_id = (
            store.get_or_create_party(db, body.party, body.party_location, body.party_cf_location,
                                      require_placement=True).id
            if body.party and body.party.strip() else None
        )
        resolved = []
        for line in body.splits:
            location = store.find_named(db, Location, line.location)
            if location is None or location.is_deleted:
                raise store.MappingError(f"Location '{line.location}' does not exist.")
            cf = None
            if line.cf_location:
                cf = store.find_named(db, CfLocation, line.cf_location)
                if cf is None or cf.is_deleted:
                    raise store.MappingError(f"CF location '{line.cf_location}' does not exist.")
            try:
                amount = Decimal(str(line.amount)).quantize(Decimal("0.01"))
            except InvalidOperation as exc:
                raise store.MappingError("Split amounts must be numbers.") from exc
            if cf is None:
                raise store.MappingError(
                    f"Split line for '{location.name}' needs its CF location - a location never "
                    "comes without one.")
            resolved.append((location.id, cf.id if cf else None, amount))
        if resolved and sum(a for _, _, a in resolved) != Decimal(txn.amount):
            raise store.MappingError(
                f"The split adds up to {sum(a for _, _, a in resolved):,.2f} but the bank amount is "
                f"{Decimal(txn.amount):,.2f}."
            )
        current = db.query(TransactionSplit).filter(TransactionSplit.txn_id == txn.id,
                                                    TransactionSplit.is_deleted == False).all()  # noqa: E712
        for old in current:
            old.is_deleted = True
        for order, (location_id, cf_id, amount) in enumerate(resolved):
            db.add(TransactionSplit(txn_id=txn.id, location_id=location_id, cf_location_id=cf_id,
                                    amount=amount, sort_order=order))
    _mapping_call(apply)
    db.commit()
    return {"ok": True}


# ── month setup (opening balances, statutory, planning block) ───────────────

@router.get("/months/{month}/setup")
def get_setup(month: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    month = _month(month)
    figures = month_figures(db, month)
    inputs = {m.cf_location_id: m for m in db.query(MonthInput).filter(
        MonthInput.month == month, MonthInput.is_deleted == False)}  # noqa: E712
    return {
        "inputs": [
            {"cf_location": row.name, "computed_opening": row.opening,
             "opening_balance": float(inputs[row.cf_id].opening_balance)
             if row.cf_id in inputs and inputs[row.cf_id].opening_balance is not None else None,
             "statutory_amount": float(inputs[row.cf_id].statutory_amount)
             if row.cf_id in inputs and inputs[row.cf_id].statutory_amount is not None else None}
            for row in figures.rows
        ],
        "plan": plan_blocks(db, month),
    }


class InputIn(BaseModel):
    cf_location: str
    opening_balance: Optional[float] = None
    statutory_amount: Optional[float] = None


class PlanItemIn(BaseModel):
    id: Optional[int] = None
    label: str
    amount_expr: str = ""


class PlanSectionIn(BaseModel):
    id: Optional[int] = None
    block_no: int = 1
    title: str = ""
    items: list[PlanItemIn] = []


class SetupIn(BaseModel):
    inputs: list[InputIn] = []
    # None = leave the stored planning block alone (the workbook no longer prints it)
    plan: Optional[list[PlanSectionIn]] = None


@router.put("/months/{month}/setup")
def save_setup(month: str, body: SetupIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    month = _month(month)

    def apply():
        from app.services.ultrafine_cashflow.summary import evaluate_amount

        existing = {m.cf_location_id: m for m in db.query(MonthInput).filter(MonthInput.month == month)}
        for entry in body.inputs:
            cf = store.find_named(db, CfLocation, entry.cf_location)
            if cf is None or cf.is_deleted:
                raise store.MappingError(f"CF location '{entry.cf_location}' does not exist.")
            row = existing.get(cf.id)
            if row is None:
                if entry.opening_balance is None and entry.statutory_amount is None:
                    continue
                row = MonthInput(month=month, cf_location_id=cf.id)
                db.add(row)
            row.opening_balance, row.statutory_amount = entry.opening_balance, entry.statutory_amount
            row.is_deleted = entry.opening_balance is None and entry.statutory_amount is None

        if body.plan is None:
            return
        sections = {s.id: s for s in db.query(PlanSection).filter(
            PlanSection.month == month, PlanSection.is_deleted == False)}  # noqa: E712
        kept_sections: set[int] = set()
        for order, section in enumerate(body.plan):
            row = sections.get(section.id) if section.id else None
            if row is None:
                row = PlanSection(month=month, block_no=max(1, section.block_no),
                                  title=clean_name(section.title), sort_order=order)
                db.add(row)
                db.flush()
            row.block_no, row.title, row.sort_order = max(1, section.block_no), clean_name(section.title), order
            kept_sections.add(row.id)
            items = {i.id: i for i in db.query(PlanItem).filter(
                PlanItem.section_id == row.id, PlanItem.is_deleted == False)}  # noqa: E712
            kept_items: set[int] = set()
            for item_order, item in enumerate(section.items):
                label = clean_name(item.label)
                if not label:
                    raise store.MappingError("Every planning item needs a label.")
                expr = (item.amount_expr or "").strip()
                if expr and evaluate_amount(expr) is None:
                    raise store.MappingError(f"'{expr}' is not a number or a simple sum such as 20+3+1.5.")
                item_row = items.get(item.id) if item.id else None
                if item_row is None:
                    item_row = PlanItem(section_id=row.id, label=label)
                    db.add(item_row)
                    db.flush()
                item_row.label, item_row.amount_expr, item_row.sort_order = label, expr, item_order
                kept_items.add(item_row.id)
            for item_id, item_row in items.items():
                if item_id not in kept_items:
                    item_row.is_deleted = True
        for section_id, row in sections.items():
            if section_id not in kept_sections:
                row.is_deleted = True
                for item in db.query(PlanItem).filter(PlanItem.section_id == section_id):
                    item.is_deleted = True
    _mapping_call(apply)
    db.commit()
    return {"ok": True}


@router.post("/months/{month}/setup/copy-plan-from/{source}")
def copy_plan(month: str, source: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    month, source = _month(month), _month(source)
    if db.query(PlanSection).filter(PlanSection.month == month, PlanSection.is_deleted == False).first():  # noqa: E712
        raise HTTPException(status_code=409, detail="This month already has a planning block.")
    blocks = plan_blocks(db, source)
    if not blocks:
        raise HTTPException(status_code=404, detail=f"{source} has no planning block to copy.")
    order = 0
    for block in blocks:
        for section in block["sections"]:
            row = PlanSection(month=month, block_no=block["block_no"], title=section["title"], sort_order=order)
            db.add(row)
            db.flush()
            for item_order, item in enumerate(section["items"]):
                db.add(PlanItem(section_id=row.id, label=item["label"], amount_expr=item["amount_expr"],
                                sort_order=item_order))
            order += 1
    db.commit()
    return {"ok": True}


# ── mappings ────────────────────────────────────────────────────────────────

@router.get("/mappings/options")
def mapping_options(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.names_for_suggestions(db)


class NameIn(BaseModel):
    name: str


class CfLocationIn(BaseModel):
    name: str
    sort_order: int = 0
    show_in_summary: bool = True
    authorized_person: str = ""
    payable_pct: float = 100
    reserve_pct: Optional[float] = None
    group: str = ""


class LocationIn(BaseModel):
    name: str
    cf_location: Optional[str] = None


class PartyIn(BaseModel):
    name: str
    location: Optional[str] = None
    cf_location: Optional[str] = None
    sharing_group: Optional[str] = None


class AliasIn(BaseModel):
    desc_key: str
    party: str


class RatioLineIn(BaseModel):
    location: str
    ratio: float
    cf_location: Optional[str] = None


class RatioSetIn(BaseModel):
    group: str
    effective_from: str
    lines: list[RatioLineIn]


class ResolveKeyIn(BaseModel):
    desc_key: str
    party: str
    location: Optional[str] = None
    cf_location: Optional[str] = None


@router.get("/mappings/cf-locations")
def get_cf_locations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.list_cf_locations(db)


@router.post("/mappings/cf-locations")
def add_cf_location(body: CfLocationIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_cf_location, None, body.model_dump())


@router.put("/mappings/cf-locations/{row_id}")
def edit_cf_location(row_id: int, body: CfLocationIn, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    return _commit(db, store.save_cf_location, row_id, body.model_dump())


@router.delete("/mappings/cf-locations/{row_id}")
def remove_cf_location(row_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _commit(db, store.archive_cf_location, row_id)
    return {"ok": True}


@router.get("/mappings/summary-groups")
def get_summary_groups(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.list_summary_groups(db)


@router.post("/mappings/summary-groups")
def add_summary_group(body: NameIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_summary_group, None, body.name)


@router.put("/mappings/summary-groups/{row_id}")
def edit_summary_group(row_id: int, body: NameIn, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    return _commit(db, store.save_summary_group, row_id, body.name)


@router.delete("/mappings/summary-groups/{row_id}")
def remove_summary_group(row_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _commit(db, store.archive_summary_group, row_id)
    return {"ok": True}


@router.get("/mappings/locations")
def get_locations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.list_locations(db)


@router.post("/mappings/locations")
def add_location(body: LocationIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_location, None, body.name, body.cf_location)


@router.put("/mappings/locations/{row_id}")
def edit_location(row_id: int, body: LocationIn, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    return _commit(db, store.save_location, row_id, body.name, body.cf_location)


@router.delete("/mappings/locations/{row_id}")
def remove_location(row_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _commit(db, store.archive_location, row_id)
    return {"ok": True}


@router.get("/mappings/parties")
def get_parties(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.list_parties(db)


@router.post("/mappings/parties")
def add_party(body: PartyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_party, None, body.name, body.location, body.cf_location, body.sharing_group)


@router.put("/mappings/parties/{row_id}")
def edit_party(row_id: int, body: PartyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_party, row_id, body.name, body.location, body.cf_location, body.sharing_group)


@router.delete("/mappings/parties/{row_id}")
def remove_party(row_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _commit(db, store.archive_party, row_id)
    return {"ok": True}


@router.get("/mappings/aliases")
def get_aliases(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.list_aliases(db)


@router.post("/mappings/aliases")
def add_alias(body: AliasIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_alias, None, body.desc_key, body.party)


@router.put("/mappings/aliases/{row_id}")
def edit_alias(row_id: int, body: AliasIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_alias, row_id, body.desc_key, body.party)


@router.delete("/mappings/aliases/{row_id}")
def remove_alias(row_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _commit(db, store.archive_alias, row_id)
    return {"ok": True}


@router.post("/mappings/resolve-key")
def resolve_key(body: ResolveKeyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Fix-up panel: point a description key at a party, creating the party
    (with its location) when it is new."""
    def apply(db_):
        party = store.get_or_create_party(db_, body.party, body.location, body.cf_location,
                                          require_placement=True)
        return store.save_alias(db_, None, body.desc_key, party.name)
    return _commit(db, apply)


class FixPartyIn(BaseModel):
    party: str
    location: Optional[str] = None
    cf_location: Optional[str] = None


@router.post("/mappings/fix-party")
def fix_party(body: FixPartyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Fix panel: complete an existing name that still lacks a location or CF
    location. Never overwrites what the name already has."""
    return _commit(db, lambda db_: {"id": store.get_or_create_party(
        db_, body.party, body.location, body.cf_location, require_placement=True).id})


@router.get("/mappings/sharing-groups")
def get_sharing_groups(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.list_sharing_groups(db)


@router.post("/mappings/sharing-groups")
def add_sharing_group(body: NameIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_sharing_group, None, body.name)


@router.put("/mappings/sharing-groups/{row_id}")
def edit_sharing_group(row_id: int, body: NameIn, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    return _commit(db, store.save_sharing_group, row_id, body.name)


@router.delete("/mappings/sharing-groups/{row_id}")
def remove_sharing_group(row_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _commit(db, store.archive_sharing_group, row_id)
    return {"ok": True}


@router.get("/mappings/ratio-sets")
def get_ratio_sets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return store.list_ratio_sets(db)


@router.post("/mappings/ratio-sets")
def add_ratio_set(body: RatioSetIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _commit(db, store.save_ratio_set, None, body.group, body.effective_from,
                   [l.model_dump() for l in body.lines])


@router.put("/mappings/ratio-sets/{row_id}")
def edit_ratio_set(row_id: int, body: RatioSetIn, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    return _commit(db, store.save_ratio_set, row_id, body.group, body.effective_from,
                   [l.model_dump() for l in body.lines])


@router.delete("/mappings/ratio-sets/{row_id}")
def remove_ratio_set(row_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _commit(db, store.archive_ratio_set, row_id)
    return {"ok": True}
