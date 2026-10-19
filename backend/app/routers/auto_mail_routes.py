"""Admin routes for an application's automatic report mail.

``make_auto_mail_router`` is included into each application's own router, so
the routes live under that application (for example
``/api/tools/untagged-invoices-report/auto-mail``):

  GET  /auto-mail            - the current settings, recipients and last run.
  PUT  /auto-mail            - save weekdays, time, recipients and wording.
  GET  /auto-mail/log        - the send history, newest first (paged).
  POST /auto-mail/send-now   - mail the report right now, as a background job
                               (poll it with the application's /jobs/{id}).

Administrators only: the mail goes to the application's whole recipient list.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.jobs import submit_job
from app.models import User
from app.services import scheduled_report_mail as mail
from app.services.mailer_shared import get_report_recipient_defaults
from app.system_mailer import get_system_email_settings


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


def make_auto_mail_router(app_key: str, job) -> APIRouter:
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

    @router.post("/send-now")
    def send_now(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        if not get_system_email_settings(db)["configured"]:
            raise HTTPException(
                status_code=400,
                detail="The system email is not set up yet. Configure it under Email administration first.",
            )
        if not get_report_recipient_defaults(db, app_key)["to"]:
            raise HTTPException(status_code=400, detail="Add at least one To recipient first, and save.")
        if mail.settings_view(db, app_key)["running"]:
            raise HTTPException(status_code=409, detail="A mail run for this report is already in progress.")
        job_id = submit_job(job, "manual", requested_by=user.email, owner_id=user.id, cancel_on_disconnect=False)
        return {"job_id": job_id}

    return router
