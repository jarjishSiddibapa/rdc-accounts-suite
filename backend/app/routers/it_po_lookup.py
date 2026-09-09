"""IT POs Lookup: PO -> ERP payment document number -> bank statement UTR.

Every route is gated by app access (require_app_access). Within the app,
role further restricts what a route can do: only 'accounts'/'both' may
upload statements or see full transaction detail; 'it' may only search and
sees a reduced field set (see _shape_result)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import (
    ORACLE_HOST, ORACLE_INSTANT_CLIENT_DIR, ORACLE_PASSWORD, ORACLE_PORT,
    ORACLE_SERVICE_NAME, ORACLE_USER, SCRATCH_DIR,
)
from app.database import get_db
from app.models import PoLookupStatementUpload, User
from app.permissions import effective_po_lookup_role, require_app_access
from app.regional import to_ist_iso
from app.services.it_po_lookup import oracle_lookup
from app.services.it_po_lookup.bank_statement_parser import parse_bank_statement
from app.services.it_po_lookup.errors import StatementParseError
from app.services.it_po_lookup.search import SearchResult, search_po_numbers
from app.services.it_po_lookup.storage import store_statement
from app.uploads import save_upload

router = APIRouter(
    prefix="/api/tools/it-po-lookup", tags=["it-po-lookup"],
    dependencies=[Depends(require_app_access("it-po-lookup"))],
)

_ORACLE_CFG = oracle_lookup.OracleConfig(
    host=ORACLE_HOST,
    port=ORACLE_PORT,
    service_name=ORACLE_SERVICE_NAME,
    user=ORACLE_USER,
    password=ORACLE_PASSWORD,
    instant_client_dir=ORACLE_INSTANT_CLIENT_DIR,
)
oracle_lookup.init_oracle_client(ORACLE_INSTANT_CLIENT_DIR)


def _require_full_access_role(user: User = Depends(get_current_user)) -> User:
    if effective_po_lookup_role(user) == "it":
        raise HTTPException(status_code=403, detail="Only Accounts or Both may upload or view bank statements.")
    return user


class SearchBody(BaseModel):
    po_numbers: str = Field(min_length=1, max_length=20_000)


def _shape_result(result: SearchResult, role: str) -> dict:
    base = {"po_number": result.po_number, "outcome": result.outcome, "vendor_name": result.vendor_name}
    if role == "it":
        # IT may only see: the PO's own vendor, whether it was found, the
        # UTR, and the three named fields - never the ERP document number
        # or raw ledger detail.
        if result.outcome == "found":
            base.update(
                utr_number=result.utr_number,
                value_date=result.value_date,
                transaction_amount=result.transaction_amount,
            )
        return base
    # 'accounts' and 'both' (and admins) see everything, including the ERP
    # document number, for reconciliation/traceability.
    base.update(
        document_number=result.document_number,
        utr_number=result.utr_number,
        transaction_date=result.transaction_date,
        value_date=result.value_date,
        transaction_description=result.transaction_description,
        transaction_amount=result.transaction_amount,
    )
    return base


@router.get("/status")
def get_status(user: User = Depends(get_current_user)):
    role = effective_po_lookup_role(user)
    return {"role": role, "can_upload": role in ("accounts", "both")}


@router.post("/search")
def search(body: SearchBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    role = effective_po_lookup_role(user)
    results = search_po_numbers(db, _ORACLE_CFG, body.po_numbers)
    return {"items": [_shape_result(r, role) for r in results]}


@router.get("/invoice-details", dependencies=[Depends(_require_full_access_role)])
def invoice_details(document_number: str):
    if not document_number.strip():
        raise HTTPException(status_code=400, detail="document_number is required.")
    invoices = oracle_lookup.fetch_invoice_details(_ORACLE_CFG, document_number.strip())
    return {"document_number": document_number.strip(), "invoices": invoices}


@router.post("/upload", dependencies=[Depends(_require_full_access_role)])
async def upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    path = await save_upload(file, SCRATCH_DIR)
    try:
        parsed = parse_bank_statement(str(path))
    except StatementParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)

    filename = file.filename or "upload.xls"
    result = store_statement(db, parsed, filename=filename, uploaded_by_id=user.id)
    return {
        "upload_id": result.upload_id,
        "filename": filename,
        "account_number": parsed.account_number,
        "from_date": parsed.from_date.isoformat() if parsed.from_date else None,
        "to_date": parsed.to_date.isoformat() if parsed.to_date else None,
        "row_count": result.row_count,
        "inserted_count": result.inserted_count,
        "duplicate_count": result.duplicate_count,
    }


@router.get("/uploads", dependencies=[Depends(_require_full_access_role)])
def uploads(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    query = db.query(PoLookupStatementUpload).filter(PoLookupStatementUpload.is_deleted.is_(False))
    total = query.count()
    rows = query.order_by(desc(PoLookupStatementUpload.uploaded_at)).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "filename": row.filename,
                "account_number": row.account_number,
                "from_date": row.from_date.isoformat() if row.from_date else None,
                "to_date": row.to_date.isoformat() if row.to_date else None,
                "row_count": row.row_count,
                "inserted_count": row.inserted_count,
                "duplicate_count": row.duplicate_count,
                "uploaded_at": to_ist_iso(row.uploaded_at),
            }
            for row in rows
        ],
    }
