"""Routes for an application's automatic report mail (administrators by default).

``make_auto_mail_router`` is included into each application's own router, so
the routes live under that application (for example
``/api/tools/untagged-invoices-report/auto-mail``):

  GET  /auto-mail            - the current settings, recipients and last run.
  PUT  /auto-mail            - save weekdays, time, recipients and wording.
  GET  /auto-mail/log        - the send history, newest first (paged).
  POST /auto-mail/send-now   - only where the application passes ``run_job``.

Administrators only unless the application passes ``access``: this controls
mail that goes out on its own. Mailing a report by hand (preview first, then send) is in report_mail_routes.py.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.jobs import submit_job
from app.models import User
from app.services import scheduled_report_mail as mail


class AutoMailBody(BaseModel):
    enabled: bool
    mode: str = "weekly"                                   # "weekly" (days) or "monthly" (month_days)
    times_enabled: bool = True                             # the days / times schedule is on
    every_hours: int | None = None                         # a second schedule: a mail every this many hours (2-23)
    every_start: str | None = None                         # ... the first one at this time of day
    days: list[int] = Field(default_factory=list)          # weekdays, 0 = Monday
    month_days: list[int] = Field(default_factory=list)    # days of the month, 0 = the last day
    time: str
    times: list[str] = Field(default_factory=list)         # every time of day; time is the earliest
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject_template: str
    intro_template: str
    signature: str = ""
    send_when_unmapped: bool = True
    sender_user_id: int | None = None   # whose own email sends it; None = the system email


def make_auto_mail_router(app_key: str, run_job=None, access=require_admin) -> APIRouter:
    """``run_job`` (optional): the application's automatic-mail job, which adds
    ``POST /auto-mail/send-now`` - the automatic mail run right now, with the
    saved settings, recipients and sender, logged like a
    scheduled run.

    ``access`` is who may use the routes: administrators, unless the
    application passes its own permission (``require_app_access``) so everyone
    granted that application can manage its automatic mail."""
    router = APIRouter(prefix="/auto-mail", dependencies=[Depends(access)])

    @router.get("")
    def get_settings(db: Session = Depends(get_db)):
        return mail.settings_view(db, app_key)

    @router.put("")
    def put_settings(body: AutoMailBody, db: Session = Depends(get_db)):
        try:
            return mail.save_settings(db, app_key, body.model_dump())
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/log")
    def get_log(limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
        return mail.log_view(db, app_key, limit=limit, offset=offset)

    if run_job is not None:
        @router.post("/send-now")
        def send_now(user: User = Depends(access)):
            job_id = submit_job(run_job, "manual", user.email, owner_id=user.id, cancel_on_disconnect=False)
            return {"job_id": job_id}

    return router
