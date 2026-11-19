"""Mail plans of the Debtors Reports Auto Mailer.

A plan is one scheduled mail: which of the three reports it sends, the date they are as on, when it goes out, who
receives it and the wording. The application can have several - a mail every morning for the team, a month-end
pack for the meeting - each with its own settings and its own send log. Nothing is shared between plans except
the mapping tables the reports use.

The date the reports are as on is one of:

  * ``today``            - the open ageing as it is when the mail is built (what the application always did).
  * ``last_month_end``   - the last day of the month before the run (that day included), worked out afresh at
                           every run, so a mail sent on 3 December is as on 30 November.
  * ``date``             - a calendar date the person picked; everything is as it stood on that day.

The schedule, run state and send log are handled by app.services.scheduled_report_mail, which treats a plan
like an application's own settings row (the columns are named alike).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.regional import today_ist
from app.services import scheduled_report_mail as mail
from app.services.mailer_shared import get_report_recipient_defaults

from . import report_choice
from .models import MailPlan

APP_KEY = "debtors-reports-auto-mailer"
MAX_PLANS = 20
MAX_NAME = 60

AS_ON_TODAY, AS_ON_LAST_MONTH_END, AS_ON_DATE = "today", "last_month_end", "date"
AS_ON_MODES = (AS_ON_TODAY, AS_ON_LAST_MONTH_END, AS_ON_DATE)
EARLIEST = date(2000, 1, 1)


class PlanError(ValueError):
    """Something that cannot be done; the message is for the person."""


# ── finding plans ────────────────────────────────────────────────────────────

def live_plans(db: Session) -> list[MailPlan]:
    return db.query(MailPlan).filter(MailPlan.is_deleted.is_(False)).order_by(MailPlan.id).all()


def get_plan(db: Session, plan_id: int) -> MailPlan | None:
    return db.query(MailPlan).filter(MailPlan.id == plan_id, MailPlan.is_deleted.is_(False)).first()


def require(db: Session, plan_id: int) -> MailPlan:
    plan = get_plan(db, plan_id)
    if plan is None:
        raise PlanError("This mail plan does not exist (it may have been deleted).")
    return plan


# ── what a plan holds ────────────────────────────────────────────────────────

def _loads(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def recipients(plan: MailPlan) -> dict:
    return {"to": _loads(plan.to_list), "cc": _loads(plan.cc_list)}


def set_recipients(plan: MailPlan, to_list: list[str], cc_list: list[str]) -> None:
    """Store the addresses, each once; an address cannot be both To and Cc."""
    to = list(dict.fromkeys(to_list))
    cc = list(dict.fromkeys(cc_list))
    both = sorted(set(to) & set(cc))
    if both:
        raise ValueError(f"{both[0]} is in both To and Cc. An address can be in one of them.")
    plan.to_list = json.dumps(to)
    plan.cc_list = json.dumps(cc)


def selected(plan: MailPlan) -> tuple[str, ...]:
    """The reports the plan sends, in the mail's order."""
    flags = {report_choice.AGEING: plan.ageing, report_choice.UNAPPLIED: plan.unapplied,
             report_choice.UNTAGGED: plan.untagged}
    return tuple(key for key in report_choice.ALL_REPORTS if flags[key]) or report_choice.ALL_REPORTS


def last_month_end(today: date) -> date:
    """The last day of the month before ``today``'s month."""
    return today.replace(day=1) - timedelta(days=1)


def resolve_as_on(mode: str, as_on_date: date | None, today: date) -> date:
    """The date the reports are as on, for a run on ``today``."""
    if mode == AS_ON_TODAY:
        return today
    if mode == AS_ON_LAST_MONTH_END:
        return last_month_end(today)
    if mode == AS_ON_DATE:
        if as_on_date is None:
            raise PlanError("Pick the date the reports are as on, or choose today or the last day of last month.")
        if as_on_date > today:
            raise PlanError("The date the reports are as on cannot be in the future.")
        return as_on_date
    raise PlanError("Choose today, the last day of last month, or a date.")


def as_on_for_run(plan: MailPlan, today: date | None = None) -> date | None:
    """What the ERP is asked for: a date, or None for the open ageing as it is now (the "today" choice, which
    is how the application always built its reports)."""
    if plan.as_on_mode == AS_ON_TODAY:
        return None
    return resolve_as_on(plan.as_on_mode, plan.as_on_date, today or today_ist())


# ── the two choices a plan makes about its content ──────────────────────────

def reports_view(plan: MailPlan) -> dict:
    chosen = selected(plan)
    return {
        "reports": [{"key": key, "label": report_choice.LABELS[key], "selected": key in chosen}
                    for key in report_choice.ALL_REPORTS],
        "selected": list(chosen),
        "all_selected": len(chosen) == len(report_choice.ALL_REPORTS),
    }


def save_reports(db: Session, plan: MailPlan, wanted, updated_by: str | None) -> dict:
    wanted = {str(key) for key in (wanted or [])}
    unknown = wanted - set(report_choice.ALL_REPORTS)
    if unknown:
        raise PlanError("Unknown report: " + ", ".join(sorted(unknown)) + ".")
    if not wanted:
        raise PlanError("Pick at least one report to send.")
    plan.ageing, plan.unapplied, plan.untagged = (key in wanted for key in report_choice.ALL_REPORTS)
    _touch(plan, updated_by)
    db.commit()
    return reports_view(plan)


def as_on_view(plan: MailPlan, today: date | None = None) -> dict:
    today = today or today_ist()
    mode = plan.as_on_mode if plan.as_on_mode in AS_ON_MODES else AS_ON_TODAY
    try:
        resolved, problem = resolve_as_on(mode, plan.as_on_date, today), None
    except PlanError as exc:
        resolved, problem = None, str(exc)
    return {
        "mode": mode,
        "as_on_date": plan.as_on_date.isoformat() if plan.as_on_date else None,
        "resolved_date": resolved.isoformat() if resolved else None,
        "problem": problem,
        "today": today.isoformat(),
        "last_month_end": last_month_end(today).isoformat(),
    }


def save_as_on(db: Session, plan: MailPlan, mode: str, as_on_date: date | None, updated_by: str | None,
               today: date | None = None) -> dict:
    today = today or today_ist()
    if mode not in AS_ON_MODES:
        raise PlanError("Choose today, the last day of last month, or a date.")
    if mode == AS_ON_DATE:
        if as_on_date is None:
            raise PlanError("Pick the date the reports are as on.")
        if as_on_date > today:
            raise PlanError("The date the reports are as on cannot be in the future.")
        if as_on_date < EARLIEST:
            raise PlanError("That date is too far back.")
        plan.as_on_date = as_on_date          # kept when the person switches back to today / last month end
    plan.as_on_mode = mode
    _touch(plan, updated_by)
    db.commit()
    return as_on_view(plan, today)


def _touch(plan: MailPlan, updated_by: str | None) -> None:
    plan.updated_at = datetime.utcnow()
    plan.updated_by = updated_by


# ── creating, renaming, copying, deleting ───────────────────────────────────

def _clean_name(db: Session, name: str, except_id: int | None = None) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise PlanError("Give the plan a name.")
    if len(name) > MAX_NAME:
        raise PlanError(f"The name can be at most {MAX_NAME} characters.")
    taken = {plan.name.casefold() for plan in live_plans(db) if plan.id != except_id}
    if name.casefold() in taken:
        raise PlanError(f"There is already a plan called \"{name}\". Choose a different name.")
    return name


def create(db: Session, name: str, updated_by: str | None, copy_of: int | None = None) -> MailPlan:
    """A new plan - switched off, so nothing goes out until it is set up and turned on. ``copy_of``: start
    from another plan's content, schedule, recipients and wording."""
    if len(live_plans(db)) >= MAX_PLANS:
        raise PlanError(f"At most {MAX_PLANS} plans. Delete one you no longer use first.")
    name = _clean_name(db, name)
    spec = mail.SPECS[APP_KEY]
    plan = MailPlan(
        name=name, enabled=False, subject_template=spec.default_subject, intro_template=spec.default_intro,
        signature="", send_when_unmapped=True, to_list="[]", cc_list="[]", created_at=datetime.utcnow(),
    )
    if copy_of is not None:
        source = require(db, copy_of)
        for column in ("ageing", "unapplied", "untagged", "as_on_mode", "as_on_date", "to_list", "cc_list",
                       "send_mode", "send_days", "send_month_days", "send_time", "send_times",
                       "send_times_enabled", "send_every_hours", "send_every_start", "subject_template",
                       "intro_template", "signature", "send_when_unmapped"):
            setattr(plan, column, getattr(source, column))
    _touch(plan, updated_by)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def rename(db: Session, plan: MailPlan, name: str, updated_by: str | None) -> MailPlan:
    plan.name = _clean_name(db, name, except_id=plan.id)
    _touch(plan, updated_by)
    db.commit()
    return plan


def delete(db: Session, plan: MailPlan, updated_by: str | None) -> None:
    """Soft-delete: it stops sending at once; its send log stays."""
    plan.is_deleted = True
    plan.enabled = False
    _touch(plan, updated_by)
    db.commit()


# ── the list ────────────────────────────────────────────────────────────────

def summary(db: Session, plan: MailPlan) -> dict:
    """What the plan list shows for one plan: the plan's schedule in the same fields the schedule card uses, so the
    page can describe it in the same words."""
    view = mail.plan_settings_view(db, plan)
    return {
        "id": plan.id,
        "name": plan.name,
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
        "reports": list(selected(plan)),
        "as_on": as_on_view(plan),
        "to_count": len(view["to"]),
        "cc_count": len(view["cc"]),
    }


def list_view(db: Session) -> dict:
    return {"plans": [summary(db, plan) for plan in live_plans(db)], "max_plans": MAX_PLANS,
            "report_labels": report_choice.LABELS}


# ── what a run needs ────────────────────────────────────────────────────────

def validate_overrides(reports, as_on_mode: str | None, as_on_date: date | None,
                       today: date | None = None) -> dict | None:
    """What a person changed for ONE send (the preview-and-send panel), checked: the reports, and/or the as-on rule
    (``today`` / ``last_month_end`` / ``date`` + the date). None when nothing was changed. Nothing is saved on the
    plan; the plan's own choices fill in whatever is left out."""
    today = today or today_ist()
    out: dict = {}
    if reports is not None:
        wanted = {str(key) for key in reports}
        unknown = wanted - set(report_choice.ALL_REPORTS)
        if unknown:
            raise PlanError("Unknown report: " + ", ".join(sorted(unknown)) + ".")
        if not wanted:
            raise PlanError("Pick at least one report to send.")
        out["reports"] = [key for key in report_choice.ALL_REPORTS if key in wanted]
    if as_on_mode is not None:
        if as_on_mode not in AS_ON_MODES:
            raise PlanError("Choose today, the last day of last month, or a date.")
        if as_on_mode == AS_ON_DATE:
            if as_on_date is None:
                raise PlanError("Pick the date the reports are as on.")
            if as_on_date < EARLIEST:
                raise PlanError("That date is too far back.")
        resolve_as_on(as_on_mode, as_on_date, today)          # a future date is refused here
        out["as_on_mode"] = as_on_mode
        out["as_on_date"] = as_on_date.isoformat() if as_on_mode == AS_ON_DATE and as_on_date else None
    return out or None


def run_options(plan_id: int, today: date | None = None, overrides: dict | None = None) -> dict:
    """Read fresh when a run starts: the plan's name, the reports, and the date for the ERP. ``overrides``: the
    one-off changes of a mail sent by hand (see validate_overrides); they win over what the plan has saved."""
    from app.database import SessionLocal

    today = today or today_ist()
    db = SessionLocal()
    try:
        plan = require(db, plan_id)
        overrides = overrides or {}
        reports = tuple(overrides["reports"]) if overrides.get("reports") else selected(plan)
        if "as_on_mode" in overrides:
            mode = overrides["as_on_mode"]
            picked = date.fromisoformat(overrides["as_on_date"]) if overrides.get("as_on_date") else None
            as_on = None if mode == AS_ON_TODAY else resolve_as_on(mode, picked, today)
        else:
            mode = plan.as_on_mode
            as_on = as_on_for_run(plan, today)
        return {"plan_name": plan.name, "reports": reports, "as_on": as_on, "as_on_mode": mode,
                "one_off": bool(overrides)}
    finally:
        db.close()


# ── bringing an existing installation across ────────────────────────────────

def migrate_legacy(db: Session) -> MailPlan | None:
    """Before plans, the application had one schedule (a row of scheduled_report_mail) and its recipients under
    Admin -> Report recipients. On the first start with plans, an installation that had that mail set up gets it
    as a plan called "Debtors Reports", so it carries on exactly as it was: same days, times, recipients and
    wording, and not sent twice (the slot it last ran is carried over). The old row is switched off."""
    from app.models import ScheduledReportMail

    if db.query(MailPlan).first() is not None:
        return None
    legacy = db.query(ScheduledReportMail).filter(ScheduledReportMail.app_key == APP_KEY).first()
    if legacy is None or legacy.is_deleted:
        return None
    people = get_report_recipient_defaults(db, APP_KEY)
    if not (legacy.enabled or people["to"] or people["cc"] or legacy.last_run_at):
        return None                          # never set up: nothing to carry across
    plan = MailPlan(
        name="Debtors Reports", as_on_mode=AS_ON_TODAY, to_list=json.dumps(people["to"]),
        cc_list=json.dumps(people["cc"]), created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    for column in ("enabled", "send_mode", "send_days", "send_month_days", "send_time", "send_times",
                   "send_times_enabled", "send_every_hours", "send_every_start", "last_every_slot",
                   "subject_template", "intro_template", "signature", "send_when_unmapped",
                   "last_attempt_date", "last_attempt_slot", "last_run_at", "last_trigger", "last_status",
                   "last_error", "last_detail"):
        setattr(plan, column, getattr(legacy, column))
    db.add(plan)
    legacy.enabled = False
    db.commit()
    return plan
