"""Routes for an application's automatic report mail (administrators by default).

``make_auto_mail_router`` is included into each application's own router, so
the routes live under that application (for example
``/api/tools/untagged-invoices-report/auto-mail``):

  GET  /auto-mail            - the current settings, recipients and last run.
  PUT  /auto-mail            - save weekdays, time, recipients and wording.
  GET  /auto-mail/log        - the send history, newest first (paged).

Administrators only unless the application passes ``access``: this controls
mail that goes out on its own. Mailing a report by hand (preview first, then send) is in report_mail_routes.py.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.services import scheduled_report_mail as mail


def current_schedule_id(request: Request) -> int | None:
    """The mail schedule a request is for, when the router is mounted under ``/schedules/{schedule_id}``; None for
    an application's own mail (the cashflow, which has no schedules)."""
    raw = request.path_params.get("schedule_id")
    return int(raw) if raw is not None else None


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
    # a schedule's report: the date it is as on, and (Debtors Reports) which reports it sends
    as_on_mode: str | None = None                          # "today", "last_month_end" or "date"
    as_on_date: date | None = None
    # the Sales Register: the days it covers - "auto", "last_month", or a "range" from period_from to period_to
    period_mode: str | None = None
    period_from: date | None = None
    period_to: date | None = None
    reports: list[str] | None = None


def make_auto_mail_router(app_key: str, access=require_admin) -> APIRouter:
    """``access`` is who may use the routes: administrators, unless the application passes its own permission
    (``require_app_access``) so everyone granted that application can manage its automatic mail."""
    router = APIRouter(prefix="/auto-mail", dependencies=[Depends(access)])

    @router.get("")
    def get_settings(db: Session = Depends(get_db), schedule_id: int | None = Depends(current_schedule_id)):
        return mail.settings_view(db, app_key, schedule_id)

    @router.put("")
    def put_settings(body: AutoMailBody, db: Session = Depends(get_db),
                     schedule_id: int | None = Depends(current_schedule_id)):
        try:
            return mail.save_settings(db, app_key, body.model_dump(), schedule_id)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/log")
    def get_log(limit: int = 20, offset: int = 0, db: Session = Depends(get_db),
                schedule_id: int | None = Depends(current_schedule_id)):
        return mail.log_view(db, app_key, limit=limit, offset=offset, schedule_id=schedule_id)

    return router
