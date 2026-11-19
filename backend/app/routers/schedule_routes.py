"""Routes for an application's mail schedules (see app.services.mail_schedules).

``make_schedules_router`` is included into each application's own router, so the routes live under that
application (for example ``/api/tools/untagged-invoices-report/schedules``):

  /schedules                      GET: the schedules.   POST: a new schedule (optionally a copy of another).
  /schedules/{id}                 GET: one schedule.   PATCH: rename.   DELETE.
  /schedules/{id}/auto-mail ...   its timing, date, reports, recipients and wording (GET / PUT), and its send log.
  /schedules/{id}/mail/...        preview its mail - with the date and reports changed for this one send if wanted -
                                  then send it by hand (the only way to send one now).
  /schedules/{id}/jobs/{job}      poll / cancel the background jobs above.

Who may change a schedule is the application's own rule (``admin_only``: administrators; otherwise anyone granted the
application). Seeing the schedules, and previewing and sending a mail by hand, is open to anyone granted the
application.
"""

from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.jobs import cancel_job, get_job
from app.models import User
from app.permissions import require_app_access
from app.routers.auto_mail_routes import make_auto_mail_router
from app.routers.report_mail_routes import make_report_mail_router
from app.services import mail_schedules


class NewScheduleBody(BaseModel):
    name: str
    copy_of: Optional[int] = None       # the id of the schedule to start from


class RenameBody(BaseModel):
    name: str


def _bad_request(db: Session, exc: Exception) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=400, detail=str(exc))


def add_job_routes(router: APIRouter) -> None:
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


def make_schedules_router(app_key: str, preview_job, admin_only: bool = False, sender_choice: bool = False,
                          add_schedule_routes: Callable[[APIRouter], None] | None = None) -> APIRouter:
    """``admin_only``: only administrators change a schedule (its timing, recipients, date). ``sender_choice``: see
    make_report_mail_router. ``add_schedule_routes``: extra routes under ``/schedules/{id}`` (the Debtors Reports'
    mapping tables, so a value found missing while previewing a schedule can be mapped on the spot)."""
    manage = require_admin if admin_only else require_app_access(app_key)
    router = APIRouter(prefix="/schedules")

    def _schedule_or_404(schedule_id: int, db: Session = Depends(get_db)) -> None:
        """Every route under /schedules/{schedule_id}: an unknown or deleted schedule is a plain 404."""
        if mail_schedules.get(db, app_key, schedule_id) is None:
            raise HTTPException(status_code=404, detail="This mail schedule does not exist (it may have been deleted).")

    @router.get("")
    def list_schedules(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        return {**mail_schedules.list_view(db, app_key), "can_manage": not admin_only or user.role == "admin"}

    @router.post("", dependencies=[Depends(manage)])
    def create_schedule(body: NewScheduleBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        try:
            row = mail_schedules.create(db, app_key, body.name, user.email, copy_of=body.copy_of)
        except ValueError as exc:
            raise _bad_request(db, exc) from exc
        return mail_schedules.summary(db, row)

    one = APIRouter(prefix="/{schedule_id}", dependencies=[Depends(_schedule_or_404)])

    @one.get("")
    def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
        return mail_schedules.summary(db, mail_schedules.require(db, app_key, schedule_id))

    @one.patch("", dependencies=[Depends(manage)])
    def rename_schedule(schedule_id: int, body: RenameBody, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
        try:
            row = mail_schedules.rename(db, mail_schedules.require(db, app_key, schedule_id), body.name, user.email)
        except ValueError as exc:
            raise _bad_request(db, exc) from exc
        return mail_schedules.summary(db, row)

    @one.delete("", dependencies=[Depends(manage)])
    def delete_schedule(schedule_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        mail_schedules.delete(db, mail_schedules.require(db, app_key, schedule_id), user.email)
        return {"ok": True}

    one.include_router(make_auto_mail_router(app_key, access=manage))
    one.include_router(make_report_mail_router(app_key, preview_job, sender_choice=sender_choice))
    add_job_routes(one)
    if add_schedule_routes is not None:
        add_schedule_routes(one)
    router.include_router(one)
    return router
