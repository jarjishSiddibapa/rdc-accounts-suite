"""Which days a Sales Register report covers, and what a person may ask for.

Every report is made of one file per calendar month (never two months in one file). What it covers is one of:

  * ``auto``        - what the report has always been: the month so far, through yesterday; and through the first
                      ``PREVIOUS_MONTH_THROUGH_DAY`` days of a month the whole previous month as well.
  * ``last_month``  - the whole of the month before the run, worked out again at every run.
  * ``range``       - the days a person picked (a month, or from one date to another).

A picked range is never longer than the automatic report can be: ONE COMPLETE MONTH PLUS THE FIRST
``PREVIOUS_MONTH_THROUGH_DAY`` DAYS OF THE NEXT. Counted from the first day of the range: it may run to the end of that
day's month and ``PREVIOUS_MONTH_THROUGH_DAY`` days beyond it, and never past yesterday. This is checked here, for
every way a period is set (a schedule, a mail sent by hand), so no page can get around it.

Plain dates only: this module imports nothing from the ERP or the mail code.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

# the previous month stays in the automatic report through this day of the new month (inclusive); a picked range may
# run this many days into the month after its first day's month
PREVIOUS_MONTH_THROUGH_DAY = 8

PERIOD_AUTO, PERIOD_LAST_MONTH, PERIOD_RANGE = "auto", "last_month", "range"
PERIOD_MODES = (PERIOD_AUTO, PERIOD_LAST_MONTH, PERIOD_RANGE)
EARLIEST = date(2000, 1, 1)


class PeriodError(ValueError):
    """A period that cannot be reported; the message is for the person."""


def _fmt(day: date) -> str:
    return f"{day.day:02d}-{day.strftime('%b-%Y')}"


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def last_month_range(today: date) -> tuple[date, date]:
    """The whole month before ``today``'s month."""
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def latest_end(start: date, today: date) -> date:
    """The last day a range starting on ``start`` may end on: the end of that month and
    ``PREVIOUS_MONTH_THROUGH_DAY`` days beyond it, but never later than yesterday."""
    return min(month_end(start) + timedelta(days=PREVIOUS_MONTH_THROUGH_DAY), today - timedelta(days=1))


def check_range(start: date | None, end: date | None, today: date) -> tuple[date, date]:
    """The range, or PeriodError saying what is wrong with it."""
    if start is None or end is None:
        raise PeriodError("Pick the first and the last day the report covers.")
    if start < EARLIEST:
        raise PeriodError("That date is too far back.")
    if end < start:
        raise PeriodError("The last day cannot be before the first day.")
    yesterday = today - timedelta(days=1)
    if start > yesterday:
        raise PeriodError(f"Only completed days can be reported. The latest first day is {_fmt(yesterday)}.")
    if end > yesterday:
        raise PeriodError(f"Only completed days can be reported. The last day can be {_fmt(yesterday)} at the latest.")
    limit = latest_end(start, today)
    if end > limit:
        raise PeriodError(
            f"A report can cover at most one complete month plus the first {PREVIOUS_MONTH_THROUGH_DAY} days of the "
            f"next. From {_fmt(start)} the last day can be {_fmt(limit)} at the latest."
        )
    return start, end


def check_period(mode: str, start: date | None, end: date | None, today: date) -> None:
    """Refuse a period choice that cannot be run."""
    if mode not in PERIOD_MODES:
        raise PeriodError("Choose the automatic period, last month, or the days you want.")
    if mode == PERIOD_RANGE:
        check_range(start, end, today)


def resolve_period(mode: str, start: date | None, end: date | None, today: date) -> tuple[date, date] | None:
    """The days to ask the ERP for on a run on ``today``: None for the automatic period (which
    ``get_report_periods`` works out), else the first and last day."""
    check_period(mode, start, end, today)
    if mode == PERIOD_LAST_MONTH:
        return last_month_range(today)
    if mode == PERIOD_RANGE:
        return start, end
    return None


def _months(start: date, end: date) -> list[tuple[str, date, date]]:
    """One (label, first, last) per calendar month the days start..end touch."""
    out, first = [], start
    while first <= end:
        last = min(month_end(first), end)
        whole = first.day == 1 and last == month_end(first)
        if whole:
            label = first.strftime("%B %Y")
        elif first == last:
            label = f"{first.day} {first.strftime('%B %Y')}"
        else:
            label = f"{first.strftime('%B %Y')} ({first.day} to {last.day})"
        out.append((label, first, last))
        first = last + timedelta(days=1)
    return out


def get_report_periods(run_date: date, period: tuple[date, date] | None = None) -> list[tuple[str, date, date]]:
    """(label, inclusive_start, inclusive_end) of what this run reports, one entry per calendar month and so one
    file per month. ``period`` (first day, last day): the days that were asked for; otherwise just the month so
    far on a normal day, but through the first ``PREVIOUS_MONTH_THROUGH_DAY`` days of a month the whole previous
    month (its own file) and then the month so far (its own file; there is none yet on the 1st)."""
    if period is not None:
        return _months(*period)
    yesterday = run_date - timedelta(days=1)
    month_start = run_date.replace(day=1)
    if run_date.day <= PREVIOUS_MONTH_THROUGH_DAY:
        previous_month_end = month_start - timedelta(days=1)
        periods = [("Previous Month", previous_month_end.replace(day=1), previous_month_end)]
        if yesterday >= month_start:
            periods.append(("Current Month MTD", month_start, yesterday))
        return periods
    return [("Current Month MTD", month_start, yesterday)]
