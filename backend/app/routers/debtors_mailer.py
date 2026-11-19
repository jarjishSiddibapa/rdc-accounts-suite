"""Debtors Reports Auto Mailer routes.

One application that builds the GST-numbered ageing, the Unapplied Receipts report and the Untagged Invoices
report from a single ERP ageing fetch and mails them (see app.services.debtors_mailer.auto_mail). It has any
number of MAIL PLANS (see app.services.debtors_mailer.plans) - each one a scheduled mail with its own reports,
as-on date, schedule, recipients, wording and send log:

  /plans                         GET: the plans.   POST: a new plan (optionally a copy of another).
  /plans/{id}                    GET: one plan.   PATCH: rename.   DELETE.
  /plans/{id}/reports            which of the three reports it sends.
  /plans/{id}/as-on              the date they are as on.
  /plans/{id}/auto-mail ...      schedule, recipients, wording, the send log, and "send now".
  /plans/{id}/mail/...           preview the plan's mail, then send it by hand.
  /plans/{id}/jobs/{job}         poll / cancel the background jobs above.
  /mappings/...                  the two mapping tables every plan's reports use (Location Name -> Location,
                                 Location -> Accounts Incharge). Also under /plans/{id}/mappings, so a value
                                 found missing while previewing a plan's mail can be mapped on the spot.

Everything is open to anyone granted the application; the mail always goes from the system email.
"""

from datetime import date
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
from app.services.debtors_mailer import auto_mail, mapping_store, plans

APP_KEY = auto_mail.APP_KEY


# ── request bodies ───────────────────────────────────────────────────────────

class LocationBody(BaseModel):
    location_name: str
    location: Optional[str] = None


class AccountsInchargeBody(BaseModel):
    location: str
    accounts_incharge: Optional[str] = None


class NewPlanBody(BaseModel):
    name: str
    copy_of: Optional[int] = None       # the id of the plan to start from


class RenameBody(BaseModel):
    name: str


class ReportsBody(BaseModel):
    reports: list[str]                  # "ageing", "unapplied", "untagged"


class AsOnBody(BaseModel):
    mode: str                           # "today", "last_month_end" or "date"
    as_on_date: Optional[date] = None   # the calendar date, when mode is "date"


def _bad_request(db: Session, exc: Exception) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=400, detail=str(exc))


def _plan_or_404(plan_id: int, db: Session = Depends(get_db)) -> None:
    """Every route under /plans/{plan_id}: unknown or deleted plans are a plain 404."""
    if plans.get_plan(db, plan_id) is None:
        raise HTTPException(status_code=404, detail="This mail plan does not exist (it may have been deleted).")


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


def _add_job_routes(router: APIRouter) -> None:
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


# ── the application ─────────────────────────────────────────────────────────

router = APIRouter(
    prefix=f"/api/tools/{APP_KEY}", tags=[APP_KEY],
    dependencies=[Depends(require_app_access(APP_KEY))],
)
_add_mapping_routes(router)
_add_job_routes(router)


@router.get("/plans")
def list_plans(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return plans.list_view(db)


@router.post("/plans")
def create_plan(body: NewPlanBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        plan = plans.create(db, body.name, user.email, copy_of=body.copy_of)
    except ValueError as exc:
        raise _bad_request(db, exc) from exc
    return plans.summary(db, plan)


# ── one plan ────────────────────────────────────────────────────────────────

plan_router = APIRouter(prefix="/plans/{plan_id}", dependencies=[Depends(_plan_or_404)])


@plan_router.get("")
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    return plans.summary(db, plans.require(db, plan_id))


@plan_router.patch("")
def rename_plan(plan_id: int, body: RenameBody, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    try:
        return plans.summary(db, plans.rename(db, plans.require(db, plan_id), body.name, user.email))
    except ValueError as exc:
        raise _bad_request(db, exc) from exc


@plan_router.delete("")
def delete_plan(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    plans.delete(db, plans.require(db, plan_id), user.email)
    return {"ok": True}


@plan_router.get("/reports")
def get_reports(plan_id: int, db: Session = Depends(get_db)):
    return plans.reports_view(plans.require(db, plan_id))


@plan_router.put("/reports")
def put_reports(plan_id: int, body: ReportsBody, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    try:
        return plans.save_reports(db, plans.require(db, plan_id), body.reports, user.email)
    except ValueError as exc:
        raise _bad_request(db, exc) from exc


@plan_router.get("/as-on")
def get_as_on(plan_id: int, db: Session = Depends(get_db)):
    return plans.as_on_view(plans.require(db, plan_id))


@plan_router.put("/as-on")
def put_as_on(plan_id: int, body: AsOnBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return plans.save_as_on(db, plans.require(db, plan_id), body.mode, body.as_on_date, user.email)
    except ValueError as exc:
        raise _bad_request(db, exc) from exc


plan_router.include_router(make_auto_mail_router(APP_KEY, run_job=auto_mail.run_auto_mail_job,
                                                 access=require_app_access(APP_KEY)))
plan_router.include_router(make_report_mail_router(APP_KEY, auto_mail.run_preview_job, sender_choice=True))
_add_mapping_routes(plan_router)
_add_job_routes(plan_router)
router.include_router(plan_router)
