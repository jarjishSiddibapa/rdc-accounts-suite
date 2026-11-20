"""Mail schedules: an application's automatic mail can have several, each its own.

A mail schedule is one scheduled mail: when it goes out, who receives it, the wording, the date its report is as on
(for the reports that can be as on a date) and - for the Debtors Reports - which of its three reports it sends. An
application can have several: a mail every morning for the team, a month-end pack for the meeting. Each has its
own settings and its own send log; nothing is shared between them except what the application itself holds (the
Debtors Reports' mapping tables).

The date the report is as on is one of:

  * ``today``            - the report as it is when the mail is built (what the applications always did).
  * ``last_month_end``   - the last day of the month before the run (that day included), worked out afresh at
                           every run, so a mail sent on 3 December is as on 30 November.
  * ``date``             - a calendar date the person picked; everything is as it stood on that day.

The Ultrafine Cashflow is of a MONTH instead: ``current`` (the month of the run), ``last`` (the month before it,
worked out afresh at every run) or ``month`` (a month the person picked, never one still to come); see resolve_month.

The date, the reports and the timing are saved together with the schedule's one Save (see
scheduled_report_mail.save_settings). The same two choices can be changed for a single mail sent by hand (the
preview-and-send panel) without touching the schedule: see validate_overrides.

The timing, run state and send log are handled by app.services.scheduled_report_mail, which treats a schedule like an
application's own settings row (the columns are named alike).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import MailSchedule, ScheduledReportMail
from app.regional import today_ist
from app.services import scheduled_report_mail as mail
from app.services.mailer_shared import get_report_recipient_defaults
from app.services.sales_register import periods as report_periods

MAX_SCHEDULES = 20
MAX_NAME = 60

AS_ON_TODAY, AS_ON_LAST_MONTH_END, AS_ON_DATE = "today", "last_month_end", "date"
AS_ON_MODES = (AS_ON_TODAY, AS_ON_LAST_MONTH_END, AS_ON_DATE)
EARLIEST = date(2000, 1, 1)

# the columns a copy of a schedule starts with
_COPIED = ("ageing", "unapplied", "untagged", "as_on_mode", "as_on_date", "period_mode", "period_from", "period_to",
           "month_mode", "month_value",
           "to_list", "cc_list", "sender_user_id",
           "send_mode", "send_days", "send_month_days", "send_time", "send_times", "send_times_enabled",
           "send_every_hours", "send_every_start", "subject_template", "intro_template", "signature",
           "send_when_unmapped")


class ScheduleError(ValueError):
    """Something that cannot be done; the message is for the person."""


# ── finding schedules ────────────────────────────────────────────────────────

def live(db: Session, app_key: str) -> list[MailSchedule]:
    return (db.query(MailSchedule).filter(MailSchedule.app_key == app_key, MailSchedule.is_deleted.is_(False))
            .order_by(MailSchedule.id).all())


def get(db: Session, app_key: str, schedule_id: int) -> MailSchedule | None:
    return (db.query(MailSchedule)
            .filter(MailSchedule.id == schedule_id, MailSchedule.app_key == app_key,
                    MailSchedule.is_deleted.is_(False)).first())


def require(db: Session, app_key: str, schedule_id: int) -> MailSchedule:
    row = get(db, app_key, schedule_id)
    if row is None:
        raise ScheduleError("This mail schedule does not exist (it may have been deleted).")
    return row


def require_id(db: Session, schedule_id: int) -> MailSchedule:
    """A live schedule of whichever application (a run is started from its id alone)."""
    row = (db.query(MailSchedule)
           .filter(MailSchedule.id == schedule_id, MailSchedule.is_deleted.is_(False)).first())
    if row is None:
        raise ScheduleError("This mail schedule does not exist (it may have been deleted).")
    return row


# ── recipients ───────────────────────────────────────────────────────────────

def _loads(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def recipients(row: MailSchedule) -> dict:
    return {"to": _loads(row.to_list), "cc": _loads(row.cc_list)}


def set_recipients(row: MailSchedule, to_list: list[str], cc_list: list[str]) -> None:
    """Store the addresses, each once; an address cannot be both To and Cc."""
    to = list(dict.fromkeys(to_list))
    cc = list(dict.fromkeys(cc_list))
    both = sorted(set(to) & set(cc))
    if both:
        raise ValueError(f"{both[0]} is in both To and Cc. An address can be in one of them.")
    row.to_list = json.dumps(to)
    row.cc_list = json.dumps(cc)


# ── the date the report is as on ────────────────────────────────────────────

def last_month_end(today: date) -> date:
    """The last day of the month before ``today``'s month."""
    return today.replace(day=1) - timedelta(days=1)


def resolve_as_on(mode: str, as_on_date: date | None, today: date) -> date:
    """The date the report is as on, for a run on ``today``."""
    if mode == AS_ON_TODAY:
        return today
    if mode == AS_ON_LAST_MONTH_END:
        return last_month_end(today)
    if mode == AS_ON_DATE:
        if as_on_date is None:
            raise ScheduleError("Pick the date the report is as on, or choose today or the last day of last month.")
        if as_on_date > today:
            raise ScheduleError("The date the report is as on cannot be in the future.")
        return as_on_date
    raise ScheduleError("Choose today, the last day of last month, or a date.")


def check_as_on(mode: str, as_on_date: date | None, today: date) -> None:
    """Refuse an as-on choice that cannot be run (a bad mode, no date, a future or absurdly old date)."""
    if mode not in AS_ON_MODES:
        raise ScheduleError("Choose today, the last day of last month, or a date.")
    if mode == AS_ON_DATE:
        if as_on_date is None:
            raise ScheduleError("Pick the date the report is as on.")
        if as_on_date < EARLIEST:
            raise ScheduleError("That date is too far back.")
    resolve_as_on(mode, as_on_date, today)           # a future date is refused here


def as_on_for_run(row: MailSchedule, today: date | None = None) -> date | None:
    """What the ERP is asked for: a date, or None for the report as it is now (the "today" choice, which is how
    the applications always built their reports)."""
    if row.as_on_mode == AS_ON_TODAY:
        return None
    return resolve_as_on(row.as_on_mode, row.as_on_date, today or today_ist())


def as_on_view(row: MailSchedule, today: date | None = None) -> dict:
    today = today or today_ist()
    mode = row.as_on_mode if row.as_on_mode in AS_ON_MODES else AS_ON_TODAY
    try:
        resolved, problem = resolve_as_on(mode, row.as_on_date, today), None
    except ScheduleError as exc:
        resolved, problem = None, str(exc)
    return {
        "mode": mode,
        "as_on_date": row.as_on_date.isoformat() if row.as_on_date else None,
        "resolved_date": resolved.isoformat() if resolved else None,
        "problem": problem,
        "today": today.isoformat(),
        "last_month_end": last_month_end(today).isoformat(),
    }


# ── the days the report covers (the Sales Register) ─────────────────────────

PERIOD_AUTO, PERIOD_LAST_MONTH, PERIOD_RANGE = (
    report_periods.PERIOD_AUTO, report_periods.PERIOD_LAST_MONTH, report_periods.PERIOD_RANGE)
PERIOD_MODES = report_periods.PERIOD_MODES


def _period_dates(data: dict) -> tuple[date | None, date | None]:
    out = []
    for key in ("period_from", "period_to"):
        value = data.get(key)
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value) if value else None
            except ValueError as exc:
                raise ScheduleError("Pick the first and the last day the report covers.") from exc
        out.append(value)
    return out[0], out[1]


def check_period(mode: str, start: date | None, end: date | None, today: date) -> None:
    """Refuse a period that cannot be run (a bad choice, missing days, the future, or more than a complete month plus
    the first 8 days of the next)."""
    try:
        report_periods.check_period(mode, start, end, today)
    except report_periods.PeriodError as exc:
        raise ScheduleError(str(exc)) from exc


def period_for_run(row: MailSchedule, today: date | None = None) -> tuple[date, date] | None:
    """What the ERP is asked for: the first and last day, or None for the automatic period."""
    try:
        return report_periods.resolve_period(
            row.period_mode or PERIOD_AUTO, row.period_from, row.period_to, today or today_ist())
    except report_periods.PeriodError as exc:
        raise ScheduleError(str(exc)) from exc


def period_view(row: MailSchedule, today: date | None = None) -> dict:
    """The schedule's period for the page: the choice, the days that were picked, what a run today would cover, and
    the limits the page works with (the same ones check_period enforces)."""
    today = today or today_ist()
    mode = row.period_mode if row.period_mode in PERIOD_MODES else PERIOD_AUTO
    try:
        picked = report_periods.resolve_period(mode, row.period_from, row.period_to, today)
        problem = None
    except report_periods.PeriodError as exc:
        picked, problem = None, str(exc)
    covers = [
        {"label": label, "from": first.isoformat(), "to": last.isoformat()}
        for label, first, last in (report_periods.get_report_periods(today, picked) if problem is None else [])
    ]
    last_from, last_to = report_periods.last_month_range(today)
    return {
        "mode": mode,
        "from": row.period_from.isoformat() if row.period_from else None,
        "to": row.period_to.isoformat() if row.period_to else None,
        "covers": covers,
        "problem": problem,
        "today": today.isoformat(),
        "yesterday": (today - timedelta(days=1)).isoformat(),
        "last_month_from": last_from.isoformat(),
        "last_month_to": last_to.isoformat(),
        "extra_days": report_periods.PREVIOUS_MONTH_THROUGH_DAY,
    }


# ── the month the report is of (the Ultrafine Cashflow) ─────────────────────

MONTH_CURRENT, MONTH_LAST, MONTH_PICK = "current", "last", "month"
MONTH_MODES = (MONTH_CURRENT, MONTH_LAST, MONTH_PICK)
_MONTH_RE = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
EARLIEST_MONTH = "2000-01"


def _month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def last_month_of(today: date) -> str:
    """The month before ``today``'s month, as YYYY-MM."""
    return _month_of(today.replace(day=1) - timedelta(days=1))


def resolve_month(mode: str, value: str | None, today: date) -> str:
    """The month (YYYY-MM) a run on ``today`` is of: this month, the month before, or the one that was picked
    (never a month still to come)."""
    if mode == MONTH_CURRENT:
        return _month_of(today)
    if mode == MONTH_LAST:
        return last_month_of(today)
    if mode == MONTH_PICK:
        if not value or not _MONTH_RE.fullmatch(str(value)):
            raise ScheduleError("Pick the month the cashflow is of.")
        if str(value) < EARLIEST_MONTH:
            raise ScheduleError("That month is too far back.")
        if str(value) > _month_of(today):
            raise ScheduleError("The cashflow of a month that has not started yet cannot be mailed.")
        return str(value)
    raise ScheduleError("Choose this month, last month, or a month to pick.")


def check_month(mode: str, value: str | None, today: date) -> None:
    """Refuse a month choice that cannot be run (a bad choice, no month, a month still to come)."""
    resolve_month(mode, value, today)


def month_for_run(row: MailSchedule, today: date | None = None) -> str:
    """The month (YYYY-MM) a run of this schedule is of; ScheduleError when the schedule's choice cannot be run."""
    return resolve_month(row.month_mode or MONTH_CURRENT, row.month_value, today or today_ist())


def month_view(row: MailSchedule, today: date | None = None) -> dict:
    """The schedule's month for the page: the choice, the month that was picked, what a run today is of."""
    today = today or today_ist()
    mode = row.month_mode if row.month_mode in MONTH_MODES else MONTH_CURRENT
    try:
        resolved, problem = resolve_month(mode, row.month_value, today), None
    except ScheduleError as exc:
        resolved, problem = None, str(exc)
    return {
        "mode": mode,
        "value": row.month_value,
        "resolved": resolved,
        "problem": problem,
        "current": _month_of(today),
        "last": last_month_of(today),
    }


# ── the choices a schedule makes about its content ──────────────────────────

def reports_selected(row: MailSchedule) -> tuple[str, ...]:
    """The Debtors Reports a schedule sends, in the mail's order (all three when none is ticked)."""
    from app.services.debtors_mailer import report_choice

    flags = {report_choice.AGEING: row.ageing, report_choice.UNAPPLIED: row.unapplied,
             report_choice.UNTAGGED: row.untagged}
    return tuple(key for key in report_choice.ALL_REPORTS if flags[key]) or report_choice.ALL_REPORTS


def _checked_reports(spec: mail.MailSpec, wanted) -> list[str]:
    allowed = [key for key, _ in spec.report_choices]
    keys = {str(key) for key in wanted}
    unknown = keys - set(allowed)
    if unknown:
        raise ScheduleError("Unknown report: " + ", ".join(sorted(unknown)) + ".")
    if not keys:
        raise ScheduleError("Pick at least one report to send.")
    return [key for key in allowed if key in keys]


def checked_content(spec: mail.MailSpec, data: dict, today: date | None = None) -> dict:
    """What a request says about a schedule's content, checked: ``as_on_mode`` / ``as_on_date`` (only for a report
    that can be as on a date) and ``reports`` (only for the Debtors Reports). Raises ScheduleError. Only what was
    sent comes back; nothing is stored."""
    today = today or today_ist()
    out: dict = {}
    if spec.as_on and data.get("as_on_mode") is not None:
        mode = str(data["as_on_mode"])
        picked = data.get("as_on_date")
        if isinstance(picked, str):
            try:
                picked = date.fromisoformat(picked)
            except ValueError as exc:
                raise ScheduleError("Pick the date the report is as on.") from exc
        check_as_on(mode, picked, today)
        out["as_on_mode"] = mode
        out["as_on_date"] = picked if mode == AS_ON_DATE else None
    if spec.period and data.get("period_mode") is not None:
        mode = str(data["period_mode"])
        start, end = _period_dates(data)
        check_period(mode, start, end, today)
        out["period_mode"] = mode
        out["period_from"], out["period_to"] = (start, end) if mode == PERIOD_RANGE else (None, None)
    if spec.month and data.get("month_mode") is not None:
        mode = str(data["month_mode"])
        value = data.get("month_value") or None
        check_month(mode, value, today)
        out["month_mode"] = mode
        out["month_value"] = str(value) if mode == MONTH_PICK else None
    if spec.report_choices and data.get("reports") is not None:
        out["reports"] = _checked_reports(spec, data["reports"])
    return out


def apply_content(row: MailSchedule, content: dict) -> None:
    """Store what checked_content returned. A fixed date is kept when the person switches to another rule."""
    if "as_on_mode" in content:
        row.as_on_mode = content["as_on_mode"]
        if content["as_on_date"] is not None:
            row.as_on_date = content["as_on_date"]
    if "period_mode" in content:
        row.period_mode = content["period_mode"]
        if content["period_from"] is not None:         # the picked days are kept when the person switches rule
            row.period_from, row.period_to = content["period_from"], content["period_to"]
    if "month_mode" in content:
        row.month_mode = content["month_mode"]
        if content["month_value"] is not None:          # the picked month is kept when the person switches rule
            row.month_value = content["month_value"]
    if "reports" in content:
        from app.services.debtors_mailer import report_choice

        row.ageing, row.unapplied, row.untagged = (key in content["reports"] for key in report_choice.ALL_REPORTS)


# ── creating, renaming, copying, deleting ───────────────────────────────────

def _clean_name(db: Session, app_key: str, name: str, except_id: int | None = None) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise ScheduleError("Give the schedule a name.")
    if len(name) > MAX_NAME:
        raise ScheduleError(f"The name can be at most {MAX_NAME} characters.")
    taken = {row.name.casefold() for row in live(db, app_key) if row.id != except_id}
    if name.casefold() in taken:
        raise ScheduleError(f"There is already a schedule called \"{name}\". Choose a different name.")
    return name


def _touch(row: MailSchedule, updated_by: str | None) -> None:
    row.updated_at = datetime.utcnow()
    row.updated_by = updated_by


def create(db: Session, app_key: str, name: str, updated_by: str | None, copy_of: int | None = None) -> MailSchedule:
    """A new schedule - switched off, so nothing goes out until it is set up and turned on. ``copy_of``: start
    from another schedule's content, timing, recipients and wording."""
    if len(live(db, app_key)) >= MAX_SCHEDULES:
        raise ScheduleError(f"At most {MAX_SCHEDULES} schedules. Delete one you no longer use first.")
    name = _clean_name(db, app_key, name)
    spec = mail.SPECS[app_key]
    row = MailSchedule(
        app_key=app_key, name=name, enabled=False, subject_template=spec.default_subject,
        intro_template=spec.default_intro, signature="", send_when_unmapped=spec.send_unmapped_default,
        to_list="[]", cc_list="[]", created_at=datetime.utcnow(),
    )
    if copy_of is not None:
        source = require(db, app_key, copy_of)
        for column in _COPIED:
            setattr(row, column, getattr(source, column))
    if spec.fixed_sender:
        row.sender_user_id = None
    _touch(row, updated_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def rename(db: Session, row: MailSchedule, name: str, updated_by: str | None) -> MailSchedule:
    row.name = _clean_name(db, row.app_key, name, except_id=row.id)
    _touch(row, updated_by)
    db.commit()
    return row


def delete(db: Session, row: MailSchedule, updated_by: str | None) -> None:
    """Soft-delete: it stops sending at once; its send log stays."""
    row.is_deleted = True
    row.enabled = False
    _touch(row, updated_by)
    db.commit()


# ── the list ────────────────────────────────────────────────────────────────

def summary(db: Session, row: MailSchedule) -> dict:
    """What the schedule list shows for one schedule: its timing in the same fields the schedule card uses, so the
    page can describe it in the same words, and its content choices."""
    spec = mail.SPECS[row.app_key]
    view = mail.schedule_settings_view(db, row)
    return {
        "id": row.id,
        "name": row.name,
        "enabled": view["enabled"],
        "mode": view["mode"],
        "days": view["days"],
        "month_days": view["month_days"],
        "times": view["times"],
        "times_enabled": view["times_enabled"],
        "every_hours": view["every_hours"],
        "every_start": view["every_start"],
        "next_run": view["next_run"],
        "running": view["running"],
        "last": {"at": view["last"]["at"], "status": view["last"]["status"], "trigger": view["last"]["trigger"]},
        "reports": list(reports_selected(row)) if spec.report_choices else None,
        "as_on": as_on_view(row) if spec.as_on else None,
        "period": period_view(row) if spec.period else None,
        "month": month_view(row) if spec.month else None,
        "to_count": len(view["to"]),
        "cc_count": len(view["cc"]),
    }


def list_view(db: Session, app_key: str) -> dict:
    spec = mail.SPECS[app_key]
    return {
        "schedules": [summary(db, row) for row in live(db, app_key)],
        "max_schedules": MAX_SCHEDULES,
        "report_labels": dict(spec.report_choices) or None,
        "has_as_on": spec.as_on,
        "has_period": spec.period,
        "has_month": spec.month,
    }


# ── what a run needs ────────────────────────────────────────────────────────

def validate_overrides(app_key: str, reports, as_on_mode: str | None, as_on_date: date | None,
                       today: date | None = None, period_mode: str | None = None,
                       period_from: date | None = None, period_to: date | None = None,
                       month_mode: str | None = None, month_value: str | None = None) -> dict | None:
    """What a person changed for ONE send (the preview-and-send panel), checked: the reports, the as-on rule
    (``today`` / ``last_month_end`` / ``date`` + the date) and/or the days the report covers (``auto`` /
    ``last_month`` / ``range`` + the first and last day). None when nothing was changed. Nothing is saved on the
    schedule; its own choices fill in whatever is left out."""
    spec = mail.SPECS[app_key]
    content = checked_content(spec, {
        "reports": reports, "as_on_mode": as_on_mode, "as_on_date": as_on_date,
        "period_mode": period_mode, "period_from": period_from, "period_to": period_to,
        "month_mode": month_mode, "month_value": month_value}, today)
    out: dict = {}
    if "reports" in content:
        out["reports"] = content["reports"]
    if "as_on_mode" in content:
        out["as_on_mode"] = content["as_on_mode"]
        out["as_on_date"] = content["as_on_date"].isoformat() if content["as_on_date"] else None
    if "period_mode" in content:
        out["period_mode"] = content["period_mode"]
        out["period_from"] = content["period_from"].isoformat() if content["period_from"] else None
        out["period_to"] = content["period_to"].isoformat() if content["period_to"] else None
    if "month_mode" in content:
        out["month_mode"] = content["month_mode"]
        out["month_value"] = content["month_value"]
    return out or None


def run_options(schedule_id: int, today: date | None = None, overrides: dict | None = None) -> dict:
    """Read fresh when a run starts: the schedule's name, the date for the ERP and (Debtors Reports) the reports.
    ``overrides``: the one-off changes of a mail sent by hand (see validate_overrides); they win over what the
    schedule has saved."""
    from app.database import SessionLocal

    today = today or today_ist()
    db = SessionLocal()
    try:
        row = require_id(db, schedule_id)
        spec = mail.SPECS[row.app_key]
        overrides = overrides or {}
        if "as_on_mode" in overrides:
            mode = overrides["as_on_mode"]
            picked = date.fromisoformat(overrides["as_on_date"]) if overrides.get("as_on_date") else None
            as_on = None if mode == AS_ON_TODAY else resolve_as_on(mode, picked, today)
        else:
            mode = row.as_on_mode
            as_on = as_on_for_run(row, today)
        if not spec.as_on:
            mode, as_on = AS_ON_TODAY, None
        options = {"schedule_name": row.name, "as_on": as_on, "as_on_mode": mode, "has_as_on": spec.as_on,
                   "one_off": bool(overrides), "has_period": spec.period, "has_month": spec.month}
        if spec.period:
            if "period_mode" in overrides:
                period_mode = overrides["period_mode"]
                picked = [date.fromisoformat(overrides[key]) if overrides.get(key) else None
                          for key in ("period_from", "period_to")]
                try:
                    options["period"] = report_periods.resolve_period(period_mode, picked[0], picked[1], today)
                except report_periods.PeriodError as exc:
                    raise ScheduleError(str(exc)) from exc
            else:
                period_mode = row.period_mode
                options["period"] = period_for_run(row, today)
            options["period_mode"] = period_mode
        if spec.month:
            if "month_mode" in overrides:
                month_mode, month_value = overrides["month_mode"], overrides.get("month_value")
            else:
                month_mode, month_value = row.month_mode or MONTH_CURRENT, row.month_value
            options["month"] = resolve_month(month_mode, month_value, today)
            options["month_mode"] = month_mode
        if spec.report_choices:
            options["reports"] = tuple(overrides["reports"]) if overrides.get("reports") else reports_selected(row)
        return options
    finally:
        db.close()


def scheduled_build(build, schedule_id: int, overrides: dict | None = None):
    """An application's builder for one of its schedules: the date (and reports) the schedule is set to are read
    fresh when the run starts and handed to ``build`` in the snapshot as ``as_on`` (a date, or None for as it is
    now) and ``reports``. The run's detail records the schedule and the as-on rule used."""
    from app.jobs import JobUserError

    def build_schedule(snapshot: dict, progress_cb=None, cancel_event=None) -> mail.MailPayload:
        try:
            options = run_options(schedule_id, overrides=overrides)
        except ScheduleError as exc:
            raise JobUserError(str(exc)) from exc
        extra = {"as_on": options["as_on"]}
        if "reports" in options:
            extra["reports"] = options["reports"]
        if options["has_period"]:
            extra["period"] = options["period"]
        if options["has_month"]:
            extra["month"] = options["month"]
        payload = build({**snapshot, **extra}, progress_cb, cancel_event)
        payload.detail["schedule"] = options["schedule_name"]
        if options["has_as_on"]:
            payload.detail["as_on_mode"] = options["as_on_mode"]
        if options["has_period"]:
            payload.detail["period_mode"] = options["period_mode"]
        if options["has_month"]:
            payload.detail["month_mode"] = options["month_mode"]
        if options["one_off"]:
            payload.detail["changed_for_this_send"] = sorted(overrides or {})
        return payload
    return build_schedule


# ── bringing an existing installation across ────────────────────────────────

def migrate_legacy(db: Session, app_key: str, name: str | None = None) -> MailSchedule | None:
    """Before schedules, an application had one (a row of scheduled_report_mail) and its recipients under Admin ->
    Report recipients. On the first start with schedules, an installation that had that mail set up gets it as a
    schedule called after the application, so it carries on exactly as it was: same days, times, recipients and
    wording, and not sent twice (the slot it last ran is carried over). The old row is switched off. An
    installation that never set it up gets one schedule, switched off, so a mail can be sent by hand from the
    start. Only an application that has never had a schedule is touched: one the person deleted stays deleted."""
    if db.query(MailSchedule).filter(MailSchedule.app_key == app_key).first() is not None:
        return None
    spec = mail.SPECS[app_key]
    legacy = db.query(ScheduledReportMail).filter(ScheduledReportMail.app_key == app_key).first()
    row = MailSchedule(
        app_key=app_key, name=name or spec.label, as_on_mode=AS_ON_TODAY, enabled=False,
        subject_template=spec.default_subject, intro_template=spec.default_intro, signature="",
        send_when_unmapped=spec.send_unmapped_default, to_list="[]", cc_list="[]", created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    people = get_report_recipient_defaults(db, app_key)
    if legacy is not None and not legacy.is_deleted and (
            legacy.enabled or people["to"] or people["cc"] or legacy.last_run_at):
        row.to_list, row.cc_list = json.dumps(people["to"]), json.dumps(people["cc"])
        for column in ("enabled", "send_mode", "send_days", "send_month_days", "send_time", "send_times",
                       "send_times_enabled", "send_every_hours", "send_every_start", "last_every_slot",
                       "subject_template", "intro_template", "signature", "send_when_unmapped", "sender_user_id",
                       "last_attempt_date", "last_attempt_slot", "last_run_at", "last_trigger", "last_status",
                       "last_error", "last_detail"):
            setattr(row, column, getattr(legacy, column))
        if not spec.send_unmapped_default:
            row.send_when_unmapped = False       # the application's own rule, not what a never-scheduled mail held
        legacy.enabled = False
    db.add(row)
    db.commit()
    return row


def migrate_all_legacy(db: Session) -> None:
    """Every application that has schedules: carry its old single schedule across (once)."""
    for app_key, spec in mail.SPECS.items():
        if spec.per_schedule:
            migrate_legacy(db, app_key, "Debtors Reports" if app_key == "debtors-reports-auto-mailer" else None)
