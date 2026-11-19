"""Debtors Reports Auto Mailer routes.

One application that builds the GST-numbered ageing, the Unapplied Receipts report and the Untagged Invoices
report from a single ERP ageing fetch and mails them (see app.services.debtors_mailer.auto_mail). It has any
number of MAIL SCHEDULES (see app.services.mail_schedules and app.routers.schedule_routes) - each one a scheduled mail
with its own reports, as-on date, timing, recipients, wording and send log:

  /schedules ...                 the schedules, and everything of one schedule (see schedule_routes).
  /mappings/...                  the two mapping tables every schedule's reports use (Location Name -> Location,
                                 Location -> Accounts Incharge). Also under /schedules/{id}/mappings, so a value
                                 found missing while previewing a schedule's mail can be mapped on the spot.

Everything is open to anyone granted the application; the mail always goes from the system email.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.permissions import require_app_access
from app.routers.schedule_routes import add_job_routes, make_schedules_router
from app.services.debtors_mailer import auto_mail, mapping_store

APP_KEY = auto_mail.APP_KEY


# ── request bodies ───────────────────────────────────────────────────────────

class LocationBody(BaseModel):
    location_name: str
    location: Optional[str] = None


class AccountsInchargeBody(BaseModel):
    location: str
    accounts_incharge: Optional[str] = None


# ── the mapping tables ──────────────────────────────────────────────────────

def _add_mapping_routes(router: APIRouter) -> None:
    # ── 1. Location Map  (key = location_name) ─────────────────────────────

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

    # ── 2. Accounts Incharge Map  (key = location) ─────────────────────────

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


# ── the application ─────────────────────────────────────────────────────────

router = APIRouter(
    prefix=f"/api/tools/{APP_KEY}", tags=[APP_KEY],
    dependencies=[Depends(require_app_access(APP_KEY))],
)
_add_mapping_routes(router)
add_job_routes(router)
router.include_router(make_schedules_router(
    APP_KEY, auto_mail.run_preview_job, sender_choice=True, add_schedule_routes=_add_mapping_routes))
