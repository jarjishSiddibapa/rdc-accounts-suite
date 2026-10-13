"""Normalized lookup keys.

A bank description such as
``NEFT  - FDRL0002301 - Rushika Enterprise - 23010200000646 - Rushika Enterprise``
changes with every payment (account numbers, IFSC codes, UTR numbers, dates)
while the counterparty stays the same. ``description_key`` removes that noise
so one alias row covers every future payment to the same counterparty, and
the party is typed exactly once.
"""

import re

_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_LONG_TOKEN = re.compile(r"\b[A-Z0-9./:]*\d{5,}[A-Z0-9./:]*\b")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# The company's own name is part of most incoming-payment narrations and says
# nothing about who paid.
_SELF_SEGMENT_PREFIXES = ("ULTRAFINE MIN",)


def casefold_key(value: str) -> str:
    """Case/whitespace-insensitive identity used for every master name."""
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def clean_name(value: str) -> str:
    """Collapse internal whitespace but keep the user's casing."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def description_key(description: str) -> str:
    text = re.sub(r"\s+", " ", str(description or "").upper()).strip()
    text = _ISO_DATE.sub(" ", text)
    text = _IFSC.sub(" ", text)
    text = _LONG_TOKEN.sub(" ", text)
    segments: list[str] = []
    for part in re.split(r"\s*-\s*", text):
        part = re.sub(r"\s+", " ", part).strip()
        if not part or part in segments:
            continue
        if part.startswith(_SELF_SEGMENT_PREFIXES):
            continue
        segments.append(part)
    return " - ".join(segments)[:500]
