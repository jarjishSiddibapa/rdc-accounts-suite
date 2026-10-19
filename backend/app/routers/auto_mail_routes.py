"""Admin routes for an application's automatic report mail.

``make_auto_mail_router`` is included into each application's own router, so
the routes live under that application (for example
``/api/tools/untagged-invoices-report/auto-mail``):

  GET  /auto-mail            - the current settings, recipients and last run.
  PUT  /auto-mail            - save weekdays, time, recipients and wording.
  GET  /auto-mail/log        - the send history, newest first (paged).

Administrators only: this controls mail that goes out on its own. Mailing a
report by hand (preview first, then send) is in report_mail_routes.py.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
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


def make_auto_mail_router(app_key: str) -> APIRouter:
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

    return router
