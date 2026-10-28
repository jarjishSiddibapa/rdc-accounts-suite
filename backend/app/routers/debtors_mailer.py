"""Debtors Reports Auto Mailer routes.

One application that builds the GST-numbered ageing, the Unapplied Receipts
report and the Untagged Invoices report from a single ERP ageing fetch and
mails them together (see app.services.debtors_mailer.auto_mail):

  /auto-mail/...   - admin: schedule (weekdays, time), recipients, wording,
                     the send log, and "send now".
  /mail/...        - anyone with access: preview the mail, then send it from
                     their own email identity.
  /jobs/{id}       - poll / cancel the background jobs above.
  /mappings/...    - the two mapping tables the reports use (Location Name ->
                     Location, Location -> Accounts Incharge).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.jobs import cancel_job, get_job
from app.models import User
from app.permissions import require_app_access
from app.routers.auto_mail_routes import make_auto_mail_router
from app.routers.report_mail_routes import make_report_mail_router
from app.services.debtors_mailer import auto_mail, mapping_store

APP_KEY = auto_mail.APP_KEY

router = APIRouter(
    prefix=f"/api/tools/{APP_KEY}", tags=[APP_KEY],
    dependencies=[Depends(require_app_access(APP_KEY))],
)

router.include_router(make_auto_mail_router(APP_KEY, run_job=auto_mail.run_auto_mail_job))
router.include_router(make_report_mail_router(APP_KEY, auto_mail.run_preview_job))


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
