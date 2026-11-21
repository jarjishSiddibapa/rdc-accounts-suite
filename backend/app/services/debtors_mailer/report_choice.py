"""The three reports the Debtors Reports Auto Mailer builds, and the mail wording that follows the ones a schedule sends.

The reports: the AR Ageing (the ageing with the GST invoice numbers), the AR Unapplied Receipts report and the AR
Untagged Invoices report (whose workbook also carries the Below 1k AR Invoices). Each mail schedule (see mail_schedules.py) picks which of them it
sends; at least one.
"""

from __future__ import annotations

import re

AGEING, UNAPPLIED, UNTAGGED = "ageing", "unapplied", "untagged"
ALL_REPORTS = (AGEING, UNAPPLIED, UNTAGGED)
LABELS = {
    AGEING: "AR Ageing",
    UNAPPLIED: "AR Unapplied Receipts",
    UNTAGGED: "AR Untagged Invoices and Below 1k AR Invoices",
}


# ── the mail's wording follows the choice ────────────────────────────────────

# The wording of the mail's default subject and text, one line per report (in the mail's order).
_SUBJECT_NAMES = {
    AGEING: ["AR Ageing"],
    UNAPPLIED: ["AR Unapplied Receipts"],
    UNTAGGED: ["AR Untagged Invoices", "Below 1k AR Invoices"],
}
_LIST_LINES = {                       # (the start of the numbered line, the report it is about)
    "ar ageing": AGEING,
    "ar unapplied": UNAPPLIED,
    "ar untagged": UNTAGGED,
    "below 1k ar": UNTAGGED,
    # the earlier wording, for a text a person saved before the reports were renamed
    "ageing with gst": AGEING,
    "unapplied": UNAPPLIED,
    "untagged": UNTAGGED,
    "below 1k": UNTAGGED,
}
_ATTACHED_NAMES = {
    AGEING: "AR Ageing",
    UNAPPLIED: "the AR Unapplied Receipts report",
    UNTAGGED: "the AR Untagged Invoices report",
}
_DEFAULT_ATTACHED = (
    "AR Ageing, the AR Unapplied Receipts report and the AR Untagged Invoices report are attached."
)
_EARLIER_ATTACHED = (         # a text saved before the reports were renamed
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
    return text.replace(_DEFAULT_ATTACHED, sentence).replace(_EARLIER_ATTACHED, sentence)
