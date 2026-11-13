"""The one setting of the Meeting Debtors Reports Auto Mailer that the Debtors Reports Auto Mailer does not
have: which date the reports are as on (see as_on.py)."""

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Integer, String

from app.database import Base


class MeetingAsOn(Base):
    """The as-on choice of one application (one row per application): a fixed calendar date, or the last day
    of the month before the run."""

    __tablename__ = "meeting_debtors_as_on"

    id         = Column(Integer, primary_key=True)
    app_key    = Column(String(64), unique=True, index=True, nullable=False)
    mode       = Column(String(20), nullable=False, default="last_month_end")   # "date" / "last_month_end"
    as_on_date = Column(Date, nullable=True)                                     # used when mode is "date"
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String(255), nullable=True)
