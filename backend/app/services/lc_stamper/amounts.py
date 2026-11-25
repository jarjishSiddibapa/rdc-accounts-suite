"""Invoice amounts on a page: reading the numbers, finding the labelled invoice total, comparing with the list.

Pure python (re only). Indian grouping (12,34,567.89), a rupee sign / "Rs." in front and "/-" behind are all fine.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# a number written like money: optional grouping commas, optional 1-2 decimals; never part of a longer token
_NUMBER_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})+|\d+)(\.\d{1,2})?(?![\w])")

# the label of the invoice total, strongest first: the page's own total beats a sub-total or a tax line
_TOTAL_LABELS = (
    r"grand\s+total", r"total\s+invoice\s+(?:value|amount)", r"invoice\s+(?:total|value|amount)",
    r"net\s+(?:amount\s+)?payable", r"amount\s+payable", r"total\s+amount(?:\s+payable)?",
    r"net\s+amount", r"bill\s+amount", r"total\s+value", r"total",
)
_LABEL_RES = tuple(re.compile(label, re.IGNORECASE) for label in _TOTAL_LABELS)
_AFTER_LABEL = 90            # characters after a label that may hold its value
_MAX_CANDIDATES = 400


def parse_amount(value) -> Optional[float]:
    """12,34,567.89 / ₹ 500 / Rs.500/- -> a float; anything that is not an amount -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"(?i)rs\.?|inr|₹|/-", " ", str(value)).strip()
    text = text.replace(",", "").replace(" ", "")
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def numbers_in(text: str) -> list:
    """Every money-like number of ``text``, in reading order."""
    out = []
    for match in _NUMBER_RE.finditer(text or ""):
        try:
            out.append(float(match.group(1).replace(",", "") + (match.group(2) or "")))
        except ValueError:
            continue
    return out


def labelled_total(text: str) -> Optional[float]:
    """The invoice total the page itself labels: the last number after its strongest total label."""
    if not text:
        return None
    for label in _LABEL_RES:
        value = None
        for found in label.finditer(text):
            tail = text[found.end():found.end() + _AFTER_LABEL]
            numbers = numbers_in(tail)
            # skip a leading percentage / count such as "Total (18%) 1,800.00": the money value is the last number
            # before the next line of the page
            first_line = tail.split("\n", 1)[0] if numbers_in(tail.split("\n", 1)[0]) else tail
            numbers = numbers_in(first_line) or numbers
            if numbers:
                value = numbers[-1] if len(numbers) > 1 else numbers[0]
        if value is not None:
            return value
    return None


def page_amounts(text: str) -> tuple:
    """The distinct amounts of a page (largest first, capped): what an invoice amount can be compared with."""
    seen = sorted({n for n in numbers_in(text) if n > 0}, reverse=True)
    return tuple(seen[:_MAX_CANDIDATES])


def amounts_match(expected: float, found: Iterable[float], tolerance: float) -> bool:
    return any(abs(expected - value) <= tolerance + 1e-9 for value in found)


def closest(expected: float, found: Iterable[float]) -> Optional[float]:
    best = None
    for value in found:
        if best is None or abs(expected - value) < abs(expected - best):
            best = value
    return best
