"""Meeting Debtors Reports Auto Mailer routes.

The Debtors Reports Auto Mailer's routes (see app.routers.debtors_mailer: automatic mail, mail by hand,
jobs, the two mapping tables) under this application's own key, plus:

  GET /as-on   the date the reports are as on: the saved choice and the date it gives today.
  PUT /as-on   save it: a calendar date, or the last day of last month.
"""

from datetime import date
from typing import Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.routers.debtors_mailer import make_router
from app.services.meeting_debtors import as_on, auto_mail

APP_KEY = auto_mail.APP_KEY

router = make_router(APP_KEY, auto_mail)


class AsOnBody(BaseModel):
    mode: str                          # "date" or "last_month_end"
    as_on_date: Optional[date] = None  # the calendar date, when mode is "date"


@router.get("/as-on")
def get_as_on(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return as_on.get_setting(db, APP_KEY)


@router.put("/as-on")
def put_as_on(body: AsOnBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return as_on.save_setting(db, APP_KEY, body.mode, body.as_on_date, user.email)
    except as_on.AsOnError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
