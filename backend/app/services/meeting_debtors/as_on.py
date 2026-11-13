"""The date the Meeting Debtors Reports Auto Mailer's reports are as on.

Two choices, saved for the application and used by every run (the automatic mail, "send now" and the
preview of a mail sent by hand):

  * ``date``            - a calendar date picked by the person; everything is as it stood on that day.
  * ``last_month_end``  - the last day of the month before the run (including that day), worked out
                          afresh at every run, so a mail sent on 3 December is as on 30 November.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.regional import today_ist

from .models import MeetingAsOn

MODE_DATE = "date"
MODE_LAST_MONTH_END = "last_month_end"
MODES = (MODE_DATE, MODE_LAST_MONTH_END)
DEFAULT_MODE = MODE_LAST_MONTH_END
EARLIEST = date(2000, 1, 1)


class AsOnError(ValueError):
    """A choice that cannot be saved or used; the message is for the person."""


def last_month_end(today: date) -> date:
    """The last day of the month before ``today``'s month."""
    return today.replace(day=1) - timedelta(days=1)


def resolve(mode: str, as_on_date: date | None, today: date) -> date:
    """The date the reports are as on, for a run on ``today``."""
    if mode == MODE_LAST_MONTH_END:
        return last_month_end(today)
    if mode == MODE_DATE:
        if as_on_date is None:
            raise AsOnError("Pick the date the reports are as on, or choose the last day of last month.")
        if as_on_date > today:
            raise AsOnError("The date the reports are as on cannot be in the future.")
        return as_on_date
    raise AsOnError("Choose a date, or the last day of last month.")


def get_setting(db: Session, app_key: str, today: date | None = None) -> dict:
    today = today or today_ist()
    row = db.query(MeetingAsOn).filter(MeetingAsOn.app_key == app_key).first()
    mode = row.mode if row is not None and row.mode in MODES else DEFAULT_MODE
    chosen = row.as_on_date if row is not None else None
    try:
        resolved = resolve(mode, chosen, today)
        problem = None
    except AsOnError as exc:
        resolved, problem = None, str(exc)
    return {
        "mode": mode,
        "as_on_date": chosen.isoformat() if chosen else None,
        "resolved_date": resolved.isoformat() if resolved else None,
        "problem": problem,
        "today": today.isoformat(),
        "last_month_end": last_month_end(today).isoformat(),
    }


def save_setting(db: Session, app_key: str, mode: str, as_on_date: date | None, updated_by: str | None,
                 today: date | None = None) -> dict:
    today = today or today_ist()
    if mode not in MODES:
        raise AsOnError("Choose a date, or the last day of last month.")
    if mode == MODE_DATE:
        if as_on_date is None:
            raise AsOnError("Pick the date the reports are as on.")
        if as_on_date > today:
            raise AsOnError("The date the reports are as on cannot be in the future.")
        if as_on_date < EARLIEST:
            raise AsOnError("That date is too far back.")
    row = db.query(MeetingAsOn).filter(MeetingAsOn.app_key == app_key).first()
    if row is None:
        row = MeetingAsOn(app_key=app_key)
        db.add(row)
    row.mode = mode
    if mode == MODE_DATE:
        row.as_on_date = as_on_date          # the last picked date is kept when switching to last month end
    row.updated_at = datetime.utcnow()
    row.updated_by = updated_by
    db.commit()
    return get_setting(db, app_key, today)


def current(app_key: str, today: date | None = None) -> tuple[str, date]:
    """(the mode, the date) a run starting now is as on. Raises AsOnError when nothing usable is saved."""
    today = today or today_ist()
    db = SessionLocal()
    try:
        view = get_setting(db, app_key, today)
    finally:
        db.close()
    if view["resolved_date"] is None:
        raise AsOnError(view["problem"] or "The date the reports are as on is not set.")
    return view["mode"], date.fromisoformat(view["resolved_date"])
