"""Which reports the Debtors Reports Auto Mailer (and the Meeting Debtors Reports Auto Mailer) send.

The mailer builds three reports: the ageing with the GST invoice numbers, the Unapplied Receipts report and the
Untagged Invoices report (whose workbook also carries the Below 1k Invoices). A person picks which of them go out;
the choice is saved per application and used by every run (the automatic mail, "send now" and a mail sent by
hand). Nothing saved means all three, as the application has always sent. At least one must be picked.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal

from .models import ReportChoice

AGEING, UNAPPLIED, UNTAGGED = "ageing", "unapplied", "untagged"
ALL_REPORTS = (AGEING, UNAPPLIED, UNTAGGED)
LABELS = {
    AGEING: "Ageing with GST Invoice Numbers",
    UNAPPLIED: "Unapplied Receipts",
    UNTAGGED: "Untagged Invoices and Below 1k Invoices",
}


class ReportChoiceError(ValueError):
    """A choice that cannot be saved; the message is for the person."""


def _view(selected: tuple[str, ...]) -> dict:
    return {
        "reports": [{"key": key, "label": LABELS[key], "selected": key in selected} for key in ALL_REPORTS],
        "selected": list(selected),
        "all_selected": len(selected) == len(ALL_REPORTS),
    }


def _selected(row) -> tuple[str, ...]:
    if row is None:
        return ALL_REPORTS
    flags = {AGEING: row.ageing, UNAPPLIED: row.unapplied, UNTAGGED: row.untagged}
    chosen = tuple(key for key in ALL_REPORTS if flags[key])
    return chosen or ALL_REPORTS


def get_setting(db: Session, app_key: str) -> dict:
    return _view(_selected(db.query(ReportChoice).filter(ReportChoice.app_key == app_key).first()))


def save_setting(db: Session, app_key: str, selected, updated_by: str | None) -> dict:
    wanted = {str(key) for key in (selected or [])}
    unknown = wanted - set(ALL_REPORTS)
    if unknown:
        raise ReportChoiceError("Unknown report: " + ", ".join(sorted(unknown)) + ".")
    if not wanted:
        raise ReportChoiceError("Pick at least one report to send.")
    row = db.query(ReportChoice).filter(ReportChoice.app_key == app_key).first()
    if row is None:
        row = ReportChoice(app_key=app_key)
        db.add(row)
    row.ageing, row.unapplied, row.untagged = (key in wanted for key in ALL_REPORTS)
    row.updated_at = datetime.utcnow()
    row.updated_by = updated_by
    db.commit()
    return get_setting(db, app_key)


def current(app_key: str) -> tuple[str, ...]:
    """The reports a run starting now sends, in the mail's order."""
    db = SessionLocal()
    try:
        return tuple(get_setting(db, app_key)["selected"])
    finally:
        db.close()


# ── the mail's wording follows the choice ────────────────────────────────────

# The wording of the mail's default subject and text, one line per report (in the mail's order).
_SUBJECT_NAMES = {
    AGEING: ["Ageing with GST Invoice Numbers"],
    UNAPPLIED: ["Unapplied Receipts"],
    UNTAGGED: ["Untagged Invoices", "Below 1k Invoices"],
}
_LIST_LINES = {                       # (the start of the numbered line, the report it is about)
    "ageing with gst": AGEING,
    "unapplied": UNAPPLIED,
    "untagged": UNTAGGED,
    "below 1k": UNTAGGED,
}
_ATTACHED_NAMES = {
    AGEING: "the ageing with the GST invoice numbers",
    UNAPPLIED: "the Unapplied Receipts report",
    UNTAGGED: "the Untagged Invoices report",
}
_DEFAULT_ATTACHED = (
    "The ageing with the GST invoice numbers, the Unapplied Receipts report and the Untagged Invoices "
    "report are attached."
)
_NUMBERED = re.compile(r"^(\s*)\d+\.\s+(.*)$")


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def adapt_subject(template: str, default_subject: str, selected) -> str:
    """The subject template, worded for the reports sent. Only the application's standard subject is rewritten;
    wording a person typed is left as it is."""
    if set(selected) == set(ALL_REPORTS) or (template or "").strip() != default_subject.strip():
        return template
    names = [name for key in ALL_REPORTS if key in selected for name in _SUBJECT_NAMES[key]]
    return f"{_join(names)} as on ({{date}})"


def adapt_intro(template: str, selected) -> str:
    """The mail text, for the reports sent: the numbered lines of the reports left out are dropped and the rest
    renumbered, and the standard "... are attached." sentence names only the attached workbooks."""
    if set(selected) == set(ALL_REPORTS):
        return template
    out, number = [], 0
    for line in (template or "").replace("\r\n", "\n").split("\n"):
        match = _NUMBERED.match(line)
        if match:
            text = match.group(2).strip().lower()
            owner = next((key for start, key in _LIST_LINES.items() if text.startswith(start)), None)
            if owner is not None and owner not in selected:
                continue
            number += 1
            line = f"{match.group(1)}{number}. {match.group(2)}"
        out.append(line)
    text = "\n".join(out)
    attached = [_ATTACHED_NAMES[key] for key in ALL_REPORTS if key in selected]
    sentence = f"{_join(attached)[0].upper()}{_join(attached)[1:]} {'is' if len(attached) == 1 else 'are'} attached."
    return text.replace(_DEFAULT_ATTACHED, sentence)
