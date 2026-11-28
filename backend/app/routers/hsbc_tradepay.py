"""RDC HSBC Tradepay CSV Generation routes (stable app key: hsbc-tradepay).

  GET  /status                  today's date, whether the HSBC ID (cell E1) is set, the next file code of a location
  PUT  /settings                set the HSBC ID (administrators)
  GET  /template                the blank Excel the payments can be typed into
  POST /upload                  a filled Excel -> the vendor code, invoice no and amount of each row
  POST /lookup                  vendor code + invoice no + amount -> every value of the file, read from the ERP
  POST /generate                check the reviewed rows against the ERP again, record and return the CSV
  GET  /files                   the files made so far (search, pages)
  GET  /files/{id}/download     a file again (its maker, or an administrator)
  DELETE /files/{id}            remove a file from the list and free its code (its maker, or an administrator)
"""

from __future__ import annotations

import re
import tempfile
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.config import SCRATCH_DIR
from app.database import get_db
from app.jobs import JobUserError
from app.models import User
from app.permissions import require_app_access
from app.services.hsbc_tradepay import erp, file_format as fmt, input_sheet, service
from app.services.hsbc_tradepay.models import HsbcTradepayFile, HsbcTradepayLine, HsbcTradepaySettings

router = APIRouter(
    prefix="/api/tools/hsbc-tradepay", tags=["hsbc-tradepay"],
    dependencies=[Depends(require_app_access("hsbc-tradepay"))],
)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_UPLOAD_LIMIT_MB = 5                                      # a filled template of 500 rows is a few tens of KB
_LOCATION_SUGGESTIONS = ("HO", "MUM", "PUN", "HYD", "BLR", "CHN", "GOA")
_NO_HSBC_ID = "The HSBC ID (cell E1 of the template) is not set yet. An administrator sets it once on this page."


class EntryIn(BaseModel):
    vendor_code: str = Field("", max_length=40)
    invoice_no: str = Field("", max_length=80)
    amount: str = Field("", max_length=40)


class LookupIn(BaseModel):
    rows: list[EntryIn] = Field(min_length=1, max_length=service.MAX_ROWS)


class PaymentIn(EntryIn):
    party_name: str = Field("", max_length=100)
    city: str = Field("", max_length=100)
    description: str = Field("", max_length=100)
    account_key: str = Field("", max_length=80)
    manual_account: str = Field("", max_length=60)
    manual_ifsc: str = Field("", max_length=30)


class GenerateIn(BaseModel):
    location: str = Field(max_length=5)
    series: int
    rows: list[PaymentIn] = Field(min_length=1, max_length=service.MAX_ROWS)


class SettingsIn(BaseModel):
    hsbc_id: str = Field(max_length=40)


# ── helpers ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _hsbc_id(db: Session) -> str:
    row = db.get(HsbcTradepaySettings, 1)
    return (row.hsbc_id or "") if row and not row.is_deleted else ""


def _person(user: User | None) -> str:
    if user is None:
        return "Unknown user"
    return " ".join(p for p in (user.first_name, user.last_name) if p) or user.email


def _next_series(db: Session, location: str) -> int:
    last = (db.query(HsbcTradepayFile.series)
            .filter(HsbcTradepayFile.location == location, HsbcTradepayFile.is_deleted.is_(False))
            .order_by(HsbcTradepayFile.series.desc()).first())
    return min(999, last[0] + 1) if last else 1


def _earlier_files(db: Session, entries: list[EntryIn]) -> dict[tuple[str, str], list[str]]:
    """{(vendor code, INVOICE NO): ["RDCCHO03 of 27-11-2026", ...]} for the listed invoices."""
    keys = sorted({erp.invoice_key(e.invoice_no) for e in entries if e.invoice_no.strip()})
    if not keys:
        return {}
    found = (db.query(HsbcTradepayLine.vendor_code, HsbcTradepayLine.invoice_key,
                      HsbcTradepayFile.file_code, HsbcTradepayFile.file_date)
             .join(HsbcTradepayFile, HsbcTradepayFile.id == HsbcTradepayLine.file_id)
             .filter(HsbcTradepayLine.invoice_key.in_(keys), HsbcTradepayLine.is_deleted.is_(False),
                     HsbcTradepayFile.is_deleted.is_(False))
             .order_by(HsbcTradepayFile.id.desc()).all())
    earlier: dict[tuple[str, str], list[str]] = {}
    for vendor, key, code, day in found:
        label = f"{code} of {day:%d-%m-%Y}"
        labels = earlier.setdefault((vendor, key), [])
        if label not in labels:
            labels.append(label)
    return earlier


def _read_erp(codes: list[str], invoices=()) -> erp.ErpData:
    try:
        return erp.fetch(codes, invoices)
    except erp.ErpUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _own_file(db: Session, file_id: int, user: User) -> HsbcTradepayFile:
    row = db.get(HsbcTradepayFile, file_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="File not found")
    if user.role != "admin" and row.created_by_id != user.id:
        raise HTTPException(status_code=403, detail="Only the person who made this file, or an administrator, can do that.")
    return row


def _csv_response(code: str, text: str) -> Response:
    return Response(
        content=text.encode("ascii"), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{code}.csv"', "Cache-Control": "no-store"},
    )


# ── routes ─────────────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/status")
def status(location: str = Query("", max_length=5), user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    bank_id = _hsbc_id(db)
    used = [row[0] for row in db.query(HsbcTradepayFile.location)
            .filter(HsbcTradepayFile.is_deleted.is_(False)).distinct().all()]
    latest = (db.query(HsbcTradepayFile.location).filter(HsbcTradepayFile.created_by_id == user.id,
                                                         HsbcTradepayFile.is_deleted.is_(False))
              .order_by(HsbcTradepayFile.id.desc()).first())
    chosen = location.strip().upper() or (latest[0] if latest else "")
    return {
        "today": date.today().isoformat(),
        "ready": bool(bank_id),
        "hsbc_id": bank_id if user.role == "admin" else "",
        "locations": sorted(set(used) | set(_LOCATION_SUGGESTIONS)),
        "location": chosen,
        "next_series": _next_series(db, chosen) if chosen else 1,
        "descriptions": list(fmt.DESCRIPTIONS),
        "max_rows": service.MAX_ROWS,
    }


@router.put("/settings")
def save_settings(body: SettingsIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        value = fmt.hsbc_id(body.hsbc_id)
    except fmt.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = db.get(HsbcTradepaySettings, 1)
    if row is None:
        row = HsbcTradepaySettings(id=1)
        db.add(row)
    row.hsbc_id, row.updated_by_id, row.updated_at, row.is_deleted = value, admin.id, datetime.utcnow(), False
    db.commit()
    return {"hsbc_id": value}


@router.get("/template")
def template():
    return Response(
        content=input_sheet.template(service.MAX_ROWS), media_type=_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{input_sheet.FILE_NAME}"', "Cache-Control": "no-store"},
    )


@router.post("/upload")
def upload(file: UploadFile = File(...)):
    data = file.file.read(_UPLOAD_LIMIT_MB * 1024 * 1024 + 1)
    if len(data) > _UPLOAD_LIMIT_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"The file is over {_UPLOAD_LIMIT_MB} MB. Upload the filled template.")
    name = re.sub(r"[^\w .()-]", "_", Path(file.filename or "payments.xlsx").name)[-80:]
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SCRATCH_DIR, ignore_cleanup_errors=True) as folder:
        path = Path(folder) / f"{uuid.uuid4()}_{name}"          # messages show the name without the prefix
        path.write_bytes(data)
        try:
            rows, notes = input_sheet.read(str(path), service.MAX_ROWS)
        except JobUserError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rows": rows, "notes": notes}


@router.post("/lookup")
def lookup(body: LookupIn, db: Session = Depends(get_db)):
    entries = [entry.model_dump() for entry in body.rows]
    codes = [service.vendor_code(e["vendor_code"]) for e in entries]
    data = _read_erp(codes, [(code, e["invoice_no"]) for code, e in zip(codes, entries)])
    return {"rows": service.review(entries, data, _earlier_files(db, body.rows))}


@router.post("/generate")
def generate(body: GenerateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bank_id = _hsbc_id(db)
    if not bank_id:
        raise HTTPException(status_code=409, detail=_NO_HSBC_ID)
    try:
        code = fmt.file_code(body.location, body.series)
        location = fmt.location_code(body.location)
    except fmt.Invalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def code_taken() -> HTTPException:
        other = db.query(HsbcTradepayFile).filter(HsbcTradepayFile.active_code == code).first()
        by = f" by {_person(db.get(User, other.created_by_id))} on {other.file_date:%d-%m-%Y}" if other else ""
        return HTTPException(status_code=409, detail=(
            f"File code {code} is already used{by}. Use series number {_next_series(db, location)}."))

    if db.query(HsbcTradepayFile.id).filter(HsbcTradepayFile.active_code == code).first():
        raise code_taken()

    entries = [row.model_dump() for row in body.rows]
    data = _read_erp([service.vendor_code(e["vendor_code"]) for e in entries])
    lines, problems = service.prepare(entries, data)
    if problems:
        more = f" (and {len(problems) - 8} more)" if len(problems) > 8 else ""
        raise HTTPException(status_code=422, detail=" ".join(problems[:8]) + more)

    today = date.today()
    text = fmt.build_csv(code, bank_id, today, [payment for _, _, payment in lines])
    record = HsbcTradepayFile(
        file_code=code, active_code=code, location=location, series=body.series, file_date=today,
        row_count=len(lines), total_amount=sum((p.amount for _, _, p in lines), Decimal(0)),
        csv_text=text, created_by_id=user.id,
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:                       # somebody else took the code a moment ago
        db.rollback()
        raise code_taken() from None
    db.add_all(HsbcTradepayLine(
        file_id=record.id, vendor_code=vendor, invoice_no=p.invoice_no, invoice_key=erp.invoice_key(p.invoice_no),
        party_name=p.party_name, amount=p.amount, account_no=p.account, ifsc=p.ifsc, account_source=source,
    ) for vendor, source, p in lines)
    db.commit()
    return _csv_response(code, text)


@router.get("/files")
def files(q: str = Query("", max_length=60), page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(HsbcTradepayFile).filter(HsbcTradepayFile.is_deleted.is_(False))
    text = q.strip()
    if text:
        like = f"%{text.upper()}%"
        matching = db.query(HsbcTradepayLine.file_id).filter(
            HsbcTradepayLine.is_deleted.is_(False),
            or_(HsbcTradepayLine.invoice_key.like(like), HsbcTradepayLine.vendor_code == text),
        )
        query = query.filter(or_(HsbcTradepayFile.file_code.like(like), HsbcTradepayFile.id.in_(matching)))
    total = query.count()
    rows = query.order_by(HsbcTradepayFile.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    people = {u.id: u for u in db.query(User).filter(User.id.in_({r.created_by_id for r in rows})).all()} if rows else {}
    admin = user.role == "admin"
    return {"total": total, "items": [{
        "id": r.id, "file_code": r.file_code, "file_date": r.file_date.isoformat(), "rows": r.row_count,
        "total": fmt.format_amount(Decimal(r.total_amount)), "made_by": _person(people.get(r.created_by_id)),
        "made_at": r.created_at.isoformat(), "mine": admin or r.created_by_id == user.id,
    } for r in rows]}


@router.get("/files/{file_id}/download")
def download(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _own_file(db, file_id, user)
    return _csv_response(row.file_code, row.csv_text)


@router.delete("/files/{file_id}", status_code=204)
def remove(file_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _own_file(db, file_id, user)
    row.is_deleted, row.active_code = True, None
    db.query(HsbcTradepayLine).filter(HsbcTradepayLine.file_id == row.id).update({HsbcTradepayLine.is_deleted: True})
    db.commit()
