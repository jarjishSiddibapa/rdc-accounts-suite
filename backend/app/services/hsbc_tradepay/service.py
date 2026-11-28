"""From the typed rows (vendor code, invoice number, amount) and the ERP to checked payment lines.

``review`` fills in everything the file needs and says what a person should look at before paying: ``errors`` stop the
row, ``warnings`` have to be ticked as checked, ``notes`` only explain how a value was filled. What concerns one bank
account travels with that account (``problem`` stops it being used, ``caution`` is a warning once it is chosen).
``prepare`` checks the reviewed rows again, against freshly read ERP data, just before the file is written: a bank
account can only be one the ERP holds as approved for that vendor, with a valid number and IFSC, and can be typed by
hand only when the ERP holds none.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from app.regional import today_ist
from app.services.hsbc_tradepay import file_format as fmt
from app.services.hsbc_tradepay.erp import ErpData, invoice_key

MAX_ROWS = 500

# Supplier type in the ERP -> "description of payment". Anything else is "Expense", flagged when the ERP has no type.
_DESCRIPTION_BY_TYPE = {
    "SUPPLIER-CEMENT": "Cement",
    "SAND AND AGGREGATES": "Aggregates",
    "SUPPLIER-FLY ASH": "Raw Material",
    "SUPPLIER-ADMIXTURE": "Raw Material",
}


def vendor_code(raw) -> str:
    """The vendor code as typed; a code Excel turned into a number ("16055.0") is put back."""
    text = str("" if raw is None else raw).strip()
    return re.sub(r"^(\d+)\.0+$", r"\1", text)


def _day(value) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def _money(value) -> str:
    return fmt.format_amount(Decimal(str(value or 0)))


# ── what the ERP says ──────────────────────────────────────────────────────────────────────────────────────────────

def _approved(row: dict) -> bool:
    deleted = str(row.get("deleted") or "0").strip()
    return str(row.get("status") or "").strip().lower() == "approved" and deleted in {"0", "0.0"}


def _account_problem(account: str, ifsc: str) -> str:
    """Why the bank would reject this ERP account ("" when it can be used)."""
    if not fmt.account_ok(account):
        wrong = f"The account number {account} is wrongly entered in the ERP."
    elif not ifsc:
        wrong = "The ERP has no IFSC for this account."
    elif not fmt.ifsc_ok(ifsc):
        wrong = f"The IFSC {ifsc} is wrongly entered in the ERP."
    else:
        return ""
    return f"{wrong} Correct it in the ERP and fetch again, or leave this payment out."


def account_options(rows: list[dict], vendor_name: str = "") -> list[dict]:
    """The distinct approved (account, IFSC) pairs of one vendor, each with where it is used."""
    options: dict[str, dict] = {}
    for row in rows:
        if not _approved(row):
            continue
        account, ifsc = fmt.clean_account(row.get("account_no")), fmt.clean_ifsc(row.get("ifsc"))
        if not account:
            continue
        key = f"{account}|{ifsc}"
        beneficiary = str(row.get("beneficiary") or "").strip()
        other = beneficiary and not same_party(beneficiary, vendor_name)
        option = options.setdefault(key, {
            "key": key, "account": account, "ifsc": ifsc, "bank": str(row.get("bank") or "").strip(),
            "branch": str(row.get("branch") or "").strip(), "locations": [], "beneficiary": beneficiary,
            "problem": _account_problem(account, ifsc),
            "caution": f"The bank account is in another name: {beneficiary}." if other else "",
        })
        location = str(row.get("location_code") or "").strip().upper()
        if location and location not in option["locations"]:
            option["locations"].append(location)
    for option in options.values():
        option["locations"].sort()
    return sorted(options.values(), key=lambda o: (o["bank"], o["account"]))


def description_for(supplier_type) -> tuple[str, str | None]:
    kind = str(supplier_type or "").strip().upper()
    if not kind:
        return "Expense", "Description set to Expense: the ERP has no supplier type for this vendor."
    return _DESCRIPTION_BY_TYPE.get(kind, "Expense"), None


def pick_invoice(candidates: list[dict]) -> dict | None:
    """Of invoices sharing the number: not cancelled first, then not fully paid, then the newest."""
    if not candidates:
        return None
    return max(candidates, key=lambda r: (
        r.get("cancelled_date") is None, str(r.get("payment_status_flag") or "N") != "Y",
        _day(r.get("invoice_date")) or date.min, r.get("invoice_id") or 0,
    ))


def choose_city(sites: list[dict], invoice_site_id=None, today: date | None = None) -> tuple[str, str | None]:
    """The city of the invoice's own site; otherwise that of the vendor's active pay sites."""
    today = today or today_ist()
    if invoice_site_id is not None:
        for site in sites:
            if site.get("vendor_site_id") == invoice_site_id:
                city = fmt.clean_city(site.get("city"))
                if city:
                    return city, None
    active = [s for s in sites if (_day(s.get("inactive_date")) or date.max) > today]
    paying = [s for s in active if s.get("pay_site_flag") == "Y"]
    cities = Counter(c for c in (fmt.clean_city(s.get("city")) for s in (paying or active or sites)) if c)
    if not cities:
        return "", "The ERP has no city for this vendor: type it."
    city = cities.most_common(1)[0][0]
    if len(cities) > 1:
        return city, f"The vendor has sites in {len(cities)} cities ({', '.join(sorted(cities)[:5])}): check the city."
    return city, None


def _invoice_checks(invoice: dict | None, paying: Decimal | None) -> tuple[dict | None, list[str]]:
    if invoice is None:
        return None, ["This invoice number is not in the ERP for this vendor: check it."]
    total = Decimal(str(invoice.get("invoice_amount") or 0))
    paid = Decimal(str(invoice.get("amount_paid") or 0))
    open_amount = total - paid
    cancelled = invoice.get("cancelled_date") is not None
    paid_full = str(invoice.get("payment_status_flag") or "") == "Y"
    day = _day(invoice.get("invoice_date"))
    info = {
        "date": day.isoformat() if day else None, "amount": _money(total), "paid": _money(paid),
        "open": _money(open_amount),
        "status": "Cancelled" if cancelled else "Paid" if paid_full else "Part paid" if paid else "Unpaid",
    }
    warnings = []
    currency = str(invoice.get("currency") or "INR").upper()
    if currency != "INR":
        warnings.append(f"The ERP invoice is in {currency}; this file pays in INR.")
    if cancelled:
        warnings.append("The ERP shows this invoice as cancelled.")
    elif paid_full:
        warnings.append("The ERP shows this invoice as already paid in full.")
    elif paying is not None and paying > open_amount:
        warnings.append(f"The amount is more than the {_money(open_amount)} still unpaid in the ERP.")
    return info, warnings


def _name_key(name: str) -> str:
    text = re.sub(r"\b(private|pvt|limited|ltd|llp|m/s)\b", " ", str(name or "").lower())
    return re.sub(r"[^a-z0-9]", "", text)


def same_party(one: str, other: str) -> bool:
    """Loosely the same name (the bank master cuts names at 40 characters and writes Pvt / Private freely)."""
    a, b = _name_key(one), _name_key(other)
    if not a or not b:
        return True
    return a[:12] == b[:12] or a in b or b in a


def _supplier_warnings(supplier: dict, today: date) -> list[str]:
    warnings = []
    ended = _day(supplier.get("end_date_active"))
    if supplier.get("enabled_flag") == "N" or (ended and ended <= today):
        warnings.append("The vendor is inactive in the ERP.")
    if supplier.get("hold_all_payments_flag") == "Y":
        warnings.append("The ERP holds all payments to this vendor.")
    return warnings


# ── the check shown before the file is made ────────────────────────────────────────────────────────────────────────

def review(entries: list[dict], erp: ErpData, earlier: dict[tuple[str, str], list[str]] | None = None,
           today: date | None = None) -> list[dict]:
    """``entries`` [{vendor_code, invoice_no, amount}]; ``earlier`` {(vendor code, NUMBER): [files it is in]}."""
    today = today or today_ist()
    earlier = earlier or {}
    rows = []
    for number, entry in enumerate(entries, 1):
        code = vendor_code(entry.get("vendor_code"))
        errors, warnings, notes = [], [], []
        row = {
            "line": number, "vendor_code": code, "invoice_no": str(entry.get("invoice_no") or "").strip(),
            "amount": str(entry.get("amount") or "").strip(), "vendor_name": "", "party_name": "", "city": "",
            "description": "", "accounts": [], "account_key": "", "manual_account": False, "invoice": None,
            "earlier_files": [], "errors": errors, "warnings": warnings, "notes": notes,
        }
        rows.append(row)
        paying = None
        try:
            row["invoice_no"] = fmt.invoice_number(entry.get("invoice_no"))
        except fmt.Invalid as exc:
            errors.append(str(exc))
        try:
            paying = fmt.amount(entry.get("amount"))
            row["amount"] = fmt.format_amount(paying)
        except fmt.Invalid as exc:
            errors.append(str(exc))
        if not code:
            errors.append("The vendor code is missing.")
            continue
        supplier = erp.suppliers.get(code)
        if supplier is None:
            errors.append(f"Vendor code {code} is not in the ERP.")
            continue

        row["vendor_name"] = str(supplier.get("vendor_name") or "").strip()
        row["party_name"], how = fmt.clean_party_name(row["vendor_name"])
        if how == "shortened":
            notes.append(f"The party name was shortened to fit {fmt.PARTY_LIMIT} characters.")
        elif how == "cut":
            notes.append(f"The party name was cut at {fmt.PARTY_LIMIT} characters.")
        warnings.extend(_supplier_warnings(supplier, today))

        invoice = pick_invoice(erp.invoices.get((supplier["vendor_id"], invoice_key(row["invoice_no"])), []))
        row["invoice"], invoice_warnings = _invoice_checks(invoice, paying)
        warnings.extend(invoice_warnings)

        row["city"], note = choose_city(
            erp.sites.get(supplier["vendor_id"], []), invoice and invoice.get("vendor_site_id"), today)
        if note:
            notes.append(note)
        row["description"], note = description_for(supplier.get("vendor_type"))
        if note:
            notes.append(note)

        bank_rows = erp.bank_rows.get(code, [])
        row["accounts"] = account_options(bank_rows, row["vendor_name"])
        if not row["accounts"]:
            row["manual_account"] = True
            warnings.append(
                ("The ERP only has disabled or deleted bank details for this vendor."
                 if bank_rows else "The ERP has no bank details for this vendor.")
                + " Type the account only after checking it against the vendor's own proof.")
        elif len(row["accounts"]) == 1:
            row["account_key"] = row["accounts"][0]["key"]

        row["earlier_files"] = earlier.get((code, invoice_key(row["invoice_no"])), [])
        if row["earlier_files"]:
            warnings.append(f"This invoice is already in file {', '.join(row['earlier_files'][:3])}.")
    return rows


# ── the lines that go into the file ────────────────────────────────────────────────────────────────────────────────

def prepare(entries: list[dict], erp: ErpData) -> tuple[list[tuple[str, str, fmt.Payment]], list[str]]:
    """([(vendor code, "erp" | "typed", payment)], problems). Nothing is silently changed: a value that differs
    from what the file may hold is reported."""
    lines, problems = [], []
    for number, entry in enumerate(entries, 1):
        code = vendor_code(entry.get("vendor_code"))
        found: list[str] = []
        invoice_no, value = "", None
        try:
            invoice_no = fmt.invoice_number(entry.get("invoice_no"))
        except fmt.Invalid as exc:
            found.append(str(exc))
        try:
            value = fmt.amount(entry.get("amount"))
        except fmt.Invalid as exc:
            found.append(str(exc))
        if code not in erp.suppliers:
            found.append(f"Vendor code {code or '(blank)'} is not in the ERP.")

        party = " ".join(str(entry.get("party_name") or "").split())
        if not party:
            found.append("The party name is missing.")
        elif len(party) > fmt.PARTY_LIMIT:
            found.append(f"The party name is longer than {fmt.PARTY_LIMIT} characters.")
        elif not re.fullmatch(r"[A-Za-z0-9 ]+", party):
            found.append("The party name has special characters.")
        city = fmt.clean_city(entry.get("city"))
        if not city:
            found.append("The city is missing.")
        description = fmt.clean_description(entry.get("description"))
        if not description:
            found.append("The description of payment is missing.")

        options = account_options(erp.bank_rows.get(code, []))
        account = ifsc = ""
        source = "erp"
        if options:
            chosen = next((o for o in options if o["key"] == entry.get("account_key")), None)
            if chosen is None:
                found.append("Choose the bank account (if it was already chosen, the ERP details have changed: "
                             "fetch the details again).")
            else:
                account, ifsc = chosen["account"], chosen["ifsc"]
                if chosen["problem"]:
                    found.append(chosen["problem"])
        else:
            source = "typed"
            account = fmt.clean_account(entry.get("manual_account"))
            ifsc = fmt.clean_ifsc(entry.get("manual_ifsc"))
            if not fmt.account_ok(account):
                found.append("Type a valid account number (letters and digits only).")
            if not fmt.ifsc_ok(ifsc):
                found.append("Type a valid IFSC (4 letters, a zero, then 6 letters or digits).")

        if found:
            problems.extend(f"Row {number} (vendor {code or '?'}): {text}" for text in found)
        else:
            lines.append((code, source, fmt.Payment(invoice_no, value, party, city, account, ifsc, description)))
    return lines, problems
