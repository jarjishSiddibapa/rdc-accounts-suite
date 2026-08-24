"""Ported byte-for-byte from the desktop app's app/mail/mail_builder.py and
app/utils/formatters.py (see
E:\\jarjish-projects\\vishal-sir-balance-confirmation-for-ultrafine).

These are real balance-confirmation emails sent to real business contacts,
so the wording, HTML structure, and Indian-style number formatting below
must not drift from what customers already receive — nothing in this file
should be "improved" or reworded without the business explicitly asking for
new wording.
"""

import html
import re
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

# The approved wording is fixed; customer, amount, and as-on-date are
# dynamic. Verbatim from app/core/constants.py's MAIL_SUBJECT_TEMPLATE.
MAIL_SUBJECT_TEMPLATE = "Balance Confirmation as of {as_on_date} - {customer_name}"


# ── Indian number formatting / amount-in-words (verbatim from formatters.py) ──

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
TEENS = ["Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def format_indian_number(value) -> str:
    amount = int(Decimal(str(value)).copy_abs().quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    digits = str(amount)
    if len(digits) <= 3:
        return digits
    tail = digits[-3:]
    head = digits[:-3]
    parts = []
    while head:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(parts + [tail])


def _under_thousand(number: int) -> str:
    words = []
    if number >= 100:
        words.extend((ONES[number // 100], "Hundred"))
        number %= 100
    if 10 <= number <= 19:
        words.append(TEENS[number - 10])
    else:
        if number >= 20:
            words.append(TENS[number // 10])
        if number % 10:
            words.append(ONES[number % 10])
    return " ".join(words)


def amount_in_indian_words(value) -> str:
    amount = int(Decimal(str(value)).copy_abs().quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount == 0:
        return "Zero"
    units = ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand"))
    words = []
    remainder = amount
    for divisor, label in units:
        part, remainder = divmod(remainder, divisor)
        if part:
            words.extend((_under_thousand(part), label))
    if remainder:
        words.append(_under_thousand(remainder))
    return " ".join(words)


def balance_description(value) -> tuple[str, str, str]:
    numeric = Decimal(str(value))
    balance_type = "debit" if numeric >= 0 else "credit"
    return balance_type, format_indian_number(numeric), amount_in_indian_words(numeric)


# ── Subject / body building (verbatim from mail_builder.py) ──────────────────

def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def normalize_as_on_date(value=None) -> tuple[str, str]:
    """Return consistent long and short display formats for a frontend date value."""
    raw = pd.Timestamp.now().strftime("%d %B %Y") if value is None else str(value).strip()
    if not raw:
        raise ValueError("As On Date cannot be blank.")
    parseable = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", raw, flags=re.IGNORECASE)
    try:
        parsed = pd.to_datetime(parseable, dayfirst=True, errors="raise")
    except Exception as exc:
        raise ValueError("Invalid As On Date. Use a date such as 30th June 2026 or 30-06-2026.") from exc
    ordinal_day = _ordinal(parsed.day)
    return (
        f"{ordinal_day} {parsed.strftime('%B %Y')}",
        f"{ordinal_day} {parsed.strftime('%B-%y')}",
    )


def build_subject(customer_name: str, balance_due=None, overdue_amount=None, as_on_date=None) -> str:
    full_date, _ = normalize_as_on_date(as_on_date)
    return MAIL_SUBJECT_TEMPLATE.format(
        as_on_date=full_date,
        customer_name=str(customer_name).strip(),
    )


def build_mail_body(customer_data: dict, signature: str = "", as_on_date=None) -> str:
    customer_name = customer_data.get("customer_name") or customer_data.get("group_name", "")
    balance = customer_data.get("balance", customer_data.get("total_balance", 0))
    balance_type, formatted_amount, amount_words = balance_description(balance)
    full_date, short_date = normalize_as_on_date(as_on_date)
    safe_signature = "<br>".join(html.escape(line) for line in (signature or "").splitlines())
    return f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#111;line-height:1.35">
<p>Dear Sir,</p>
<p>Please give us sign copy of balance confirmation as of {short_date}. Required for debtors confirmation and audit purposes.</p>
<p>In our books as of {full_date}, There is {balance_type} balance of <strong>Rs.{formatted_amount}/- (Rupees {amount_words} Only).</strong><br>
If there is any mismatch, please mention the balance as per your books and share the ledger for reconciliation.</p>
<p>{safe_signature}</p>
</body></html>"""
