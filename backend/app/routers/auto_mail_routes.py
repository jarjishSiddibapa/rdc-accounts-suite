"""Admin routes for an application's automatic report mail.

``make_auto_mail_router`` is included into each application's own router, so
the routes live under that application (for example
``/api/tools/untagged-invoices-report/auto-mail``):

  GET  /auto-mail            - the current settings, recipients and last run.
  PUT  /auto-mail            - save weekdays, time, recipients and wording.
  GET  /auto-mail/log        - the send history, newest first (paged).
  POST /auto-mail/send-now   - only where the application passes ``run_job``.

Administrators only: this controls mail that goes out on its own. Mailing a
report by hand (preview first, then send) is in report_mail_routes.py.
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
    days: list[int] = Field(default_factory=list)
    time: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject_template: str
    intro_template: str
    signature: str = ""
    send_when_unmapped: bool = True
    sender_user_id: int | None = None   # whose own email sends it; None = the system email


def make_auto_mail_router(app_key: str, run_job=None) -> APIRouter:
    """``run_job`` (optional): the application's automatic-mail job, which adds
    ``POST /auto-mail/send-now`` - the automatic mail run right now, with the
    saved settings, recipients and sender, logged like a
    scheduled run."""
    router = APIRouter(prefix="/auto-mail", dependencies=[Depends(require_admin)])

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
        def send_now(user: User = Depends(require_admin)):
            job_id = submit_job(run_job, "manual", user.email, owner_id=user.id, cancel_on_disconnect=False)
            return {"job_id": job_id}

    return router
