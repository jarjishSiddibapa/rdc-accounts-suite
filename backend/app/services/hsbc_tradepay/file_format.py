"""The HSBC TradePay upload file: its fixed layout and the rules every value in it must follow.

The layout is the Accounts team's own Excel template saved as CSV: 19 columns (A to S) on every line, CRLF line
ends. Only the cells the template highlights in yellow change; every other text is copied as it is.

    H , TWC , <file code> , IN , <HSBC id> , V , (blank) , Y
    BH, <file code> , , INR
    DT, <invoice no> , <file date yyyymmdd> , INR , <amount> , <party> , <city> , IN , <city> , <city> , INIFSCCD ,
        , IN , , , <account no> , , <IFSC> , <description>

The template's guideline asks for the account number "preceded by an apostrophe". That is Excel's way of keeping
the digits as text: the apostrophe is not part of the cell and Excel does not write it to the CSV, so neither does
this module.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

COLUMNS = 19
PARTY_LIMIT = 35
CITY_LIMIT = 35
DESCRIPTION_LIMIT = 70
INVOICE_LIMIT = 50
FILE_CODE_PREFIX = "RDCC"
DESCRIPTIONS = ("Raw Material", "Cement", "Aggregates", "Expense")

_LOCATION = re.compile(r"^[A-Z]{2,3}$")
_HSBC_ID = re.compile(r"^[A-Z0-9]{6,30}$")
_ACCOUNT = re.compile(r"^[A-Za-z0-9]{5,34}$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_AMOUNT = re.compile(r"^\d{1,12}(\.\d+)?$")

# Applied in this order, one at a time, only until the name fits in PARTY_LIMIT.
_SHORT_FORMS = (
    (r"private limited", "Pvt Ltd"), (r"limited", "Ltd"), (r"private", "Pvt"), (r"and company", "and Co"),
    (r"company", "Co"), (r"corporation", "Corp"), (r"international", "Intl"), (r"enterprises?", "Entp"),
    (r"industries", "Inds"), (r"engineering", "Engg"), (r"engineers", "Engrs"), (r"services", "Svcs"),
    (r"associates", "Assoc"), (r"constructions?", "Constn"), (r"infrastructure", "Infra"), (r"transports?", "Trpt"),
    (r"manufacturing", "Mfg"), (r"technologies", "Tech"), (r"brothers", "Bros"), (r"solutions", "Soln"),
    (r"distributors", "Distr"),
)


class Invalid(ValueError):
    """A value that cannot go into the file; the message is meant for the user."""


@dataclass(frozen=True)
class Payment:
    """One DT line, already checked."""

    invoice_no: str
    amount: Decimal
    party_name: str
    city: str
    account: str
    ifsc: str
    description: str


def _ascii(value) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()


def clean_party_name(raw) -> tuple[str, str | None]:
    """The name with no special characters, within 35 characters.

    Returns (name, how): how is None when the name already fitted, "shortened" when standard short forms made it
    fit, "cut" when words had to be dropped from the end."""
    text = re.sub(r"^\s*m\s*/\s*s\b\.?\s*", "", _ascii(raw), flags=re.IGNORECASE)
    text = text.replace("&", " and ")
    text = re.sub(r"['`]", "", text)
    text = " ".join(re.sub(r"[^A-Za-z0-9 ]+", " ", text).split())
    if len(text) <= PARTY_LIMIT:
        return text, None
    for words, short in _SHORT_FORMS:
        text = re.sub(rf"\b{words}\b", lambda m, s=short: s.upper() if m.group().isupper() else s, text,
                      flags=re.IGNORECASE)
        if len(text) <= PARTY_LIMIT:
            return text, "shortened"
    cut = text[:PARTY_LIMIT]
    if text[PARTY_LIMIT] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip(), "cut"


def clean_city(raw) -> str:
    """Letters and spaces only, in title case: "BANGALORE-1" -> "Bangalore"."""
    text = " ".join(re.sub(r"[^A-Za-z ]+", " ", _ascii(raw)).split())
    return text.title()[:CITY_LIMIT].strip()


def clean_description(raw) -> str:
    text = " ".join(re.sub(r"[^A-Za-z0-9 &/()._-]+", " ", _ascii(raw)).split())
    return text.lstrip("-").strip()[:DESCRIPTION_LIMIT].strip()


def invoice_number(raw) -> str:
    """The invoice number as it goes into the file (kept as typed, only checked)."""
    text = str(raw or "").strip()
    if not text:
        raise Invalid("The invoice number is missing.")
    if len(text) > INVOICE_LIMIT:
        raise Invalid(f"The invoice number is longer than {INVOICE_LIMIT} characters.")
    if any(c in text for c in ',"') or not all(32 <= ord(c) < 127 for c in text):
        raise Invalid("The invoice number has a comma, a quote or a character the bank file cannot hold.")
    if text[0] in "=+-@":
        raise Invalid("The invoice number cannot start with =, +, - or @.")
    return text


def amount(raw) -> Decimal:
    """A positive amount with at most two decimals; "1,25,000.00" and "Rs. 125000" are understood."""
    if isinstance(raw, float):
        raw = repr(raw)
    text = re.sub(r"^(rs\.?|inr|₹)\s*", "", str("" if raw is None else raw).strip(), flags=re.IGNORECASE)
    text = text.replace(",", "").replace(" ", "")
    if not text:
        raise Invalid("The amount is missing.")
    if not _AMOUNT.match(text):
        raise Invalid(f'"{str(raw).strip()}" is not a payment amount.')
    value = Decimal(text)
    if value != value.quantize(Decimal("0.01")):
        raise Invalid("The amount has more than two decimals.")
    if value <= 0:
        raise Invalid("The amount must be more than zero.")
    return value.quantize(Decimal("0.01"))


def format_amount(value: Decimal) -> str:
    """As Excel writes a number: 125000, 125000.5, 125000.55."""
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".")


def clean_account(raw) -> str:
    return re.sub(r"[\s'`]+", "", str(raw or ""))


def clean_ifsc(raw) -> str:
    return re.sub(r"[\s'`]+", "", str(raw or "")).upper()


def account_ok(value: str) -> bool:
    return bool(_ACCOUNT.match(value))


def ifsc_ok(value: str) -> bool:
    return bool(_IFSC.match(value))


def location_code(raw) -> str:
    text = str(raw or "").strip().upper()
    if not _LOCATION.match(text):
        raise Invalid("The location in the file code is 2 or 3 letters, for example HO or HYD.")
    return text


def file_code(location, series) -> str:
    """"RDCC" + location + series number, e.g. RDCCHYD01."""
    try:
        number = int(series)
    except (TypeError, ValueError):
        raise Invalid("The file series number is a whole number, for example 1.") from None
    if not 1 <= number <= 999:
        raise Invalid("The file series number is between 1 and 999.")
    return f"{FILE_CODE_PREFIX}{location_code(location)}{number:02d}"


def hsbc_id(raw) -> str:
    text = str(raw or "").strip().upper()
    if not _HSBC_ID.match(text):
        raise Invalid("The HSBC ID is 6 to 30 letters and digits, exactly as in cell E1 of the template.")
    return text


def build_csv(code: str, bank_id: str, file_date: date, payments: list[Payment]) -> str:
    if not payments:
        raise Invalid("There is no payment to put in the file.")
    when = file_date.strftime("%Y%m%d")
    rows = [
        ["H", "TWC", code, "IN", bank_id, "V", "", "Y"],
        ["BH", code, "", "INR"],
    ]
    for p in payments:
        rows.append([
            "DT", p.invoice_no, when, "INR", format_amount(p.amount), p.party_name, p.city, "IN", p.city, p.city,
            "INIFSCCD", "", "IN", "", "", p.account, "", p.ifsc, p.description,
        ])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    for row in rows:
        writer.writerow(row + [""] * (COLUMNS - len(row)))
    return out.getvalue()
