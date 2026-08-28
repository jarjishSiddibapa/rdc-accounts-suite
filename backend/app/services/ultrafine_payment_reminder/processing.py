"""Ported from ultrafine-bulk-payment-reminder's app/excel/excel_reader.py
and app/utils/validators.py, plus the exact-filename PDF-attachment lookup
originally done in app/ui/main_window.py's select_attachment_folder /
send_mails ("{Customer Name}.pdf" against a chosen folder).

Column names, the required-column lists, and the merge/uniqueness rules are
carried over byte-for-byte from the desktop app. The one behavioral
addition is the optional persistent CustomerEmailMap fallback in
merge_and_group_data below: the desktop app always required a fresh Emails
Excel upload for every send, while the suite lets that upload be entirely
skipped (send using only the saved mapping) or provided as a partial
override (only the customers actually present in it take precedence,
everyone else falls back to the saved mapping).
"""

import math
import mimetypes
import os
import re
import smtplib
import threading
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import dns.resolver
import pandas as pd
from email_validator import EmailNotValidError, validate_email

from app.jobs import JobUserError
from app.services.mailer_shared import read_attachment_bytes

# ── Excel readers (verbatim from excel_reader.py) ──────────────────────────


def normalize_header(header) -> str:
    """Remove newlines and extra spaces from header."""
    return str(header).strip().replace("\n", " ")


DATA_REQUIRED_COLUMNS = [
    "Customer Name",
    "Credit Days", "Overdue", "Net O/s", "Unapplied Amt", "Accounted O/s",
    "0-30", "31-60", "61-90", "91-120", "121-150", "151-180", "181-360", "361-999999",
]

EMAILS_REQUIRED_COLUMNS = ["Customer Name", "To Email IDs", "CC Email IDs"]


def read_data_excel(file_path: str) -> pd.DataFrame:
    """Reads the financial/aging data excel. Raises ValueError with the same
    wording as the desktop app on missing columns or duplicate customers."""
    df = pd.read_excel(file_path, engine="openpyxl")
    df.columns = [normalize_header(c) for c in df.columns]

    missing = [c for c in DATA_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise JobUserError(
            f"Missing columns in Data File: {', '.join(missing)}\n"
            "Please ensure the file format matches strict requirements."
        )

    if not df["Customer Name"].is_unique:
        duplicates = df[df.duplicated("Customer Name", keep=False)]["Customer Name"].unique()
        dup_str = ", ".join(map(str, duplicates[:10]))
        raise JobUserError(
            f"Duplicate Customer Names found in Data File: {dup_str}...\n"
            "Each Customer Name must be unique."
        )

    return df


def read_emails_excel(file_path: str) -> pd.DataFrame:
    """Reads the emails mapping excel."""
    df = pd.read_excel(file_path, engine="openpyxl")
    df.columns = [normalize_header(c) for c in df.columns]

    missing = [c for c in EMAILS_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise JobUserError(f"Missing required columns in Emails File: {', '.join(missing)}")

    df["To Email IDs"] = df["To Email IDs"].fillna("").astype(str).str.strip()
    if "CC Email IDs" not in df.columns:
        df["CC Email IDs"] = ""
    else:
        df["CC Email IDs"] = df["CC Email IDs"].fillna("").astype(str).str.strip()

    if not df["Customer Name"].is_unique:
        duplicates = df[df.duplicated("Customer Name", keep=False)]["Customer Name"].unique()
        dup_str = ", ".join(map(str, duplicates[:10]))
        raise JobUserError(
            f"Duplicate Customer Names found in Emails File: {dup_str}...\n"
            "Each Customer Name must appear only once."
        )

    return df


# ── merge / group (adapted from merge_and_group_data) ──────────────────────


def _safe_str(val, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return str(val).strip()


def _safe_float(val, default: float = 0.0) -> float:
    try:
        f = float(val)
        if math.isnan(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def is_blank_email_value(value: str) -> bool:
    v = (value or "").strip()
    return v == "" or v == "-" or v.lower() == "nan"


def merge_and_group_data(
    data_df: pd.DataFrame,
    emails_df: Optional[pd.DataFrame],
    saved_mapping: Optional[dict[str, dict[str, str]]] = None,
) -> list[dict]:
    """Merges the data file with the emails file (when supplied) exactly
    like the desktop app's merge_and_group_data, then falls back to the
    persistent CustomerEmailMap (`saved_mapping`, from
    mapping_store.load_all) for any customer whose To/CC came up blank —
    covering both "no emails file uploaded at all" and "customer missing
    from the uploaded emails file" in a single pass.

    Returns a list of dicts: {group_name, data_rows, total_balance,
    total_overdue, to_emails, cc_emails} — the same shape the desktop app
    used, so mail_builder.build_mail_body needs zero changes.
    """
    saved_mapping = saved_mapping or {}

    if emails_df is not None:
        merged_df = pd.merge(data_df, emails_df, on="Customer Name", how="left")
    else:
        merged_df = data_df.copy()
        merged_df["To Email IDs"] = ""
        merged_df["CC Email IDs"] = ""

    groups = []
    for _, row in merged_df.iterrows():
        customer_name = _safe_str(row["Customer Name"], "Unknown")
        to_emails = _safe_str(row.get("To Email IDs", ""))
        cc_emails = _safe_str(row.get("CC Email IDs", ""))

        saved = saved_mapping.get(customer_name, {})
        if is_blank_email_value(to_emails):
            to_emails = _safe_str(saved.get("to_emails", ""))
        if is_blank_email_value(cc_emails):
            cc_emails = _safe_str(saved.get("cc_emails", ""))

        total_balance = _safe_float(row["Net O/s"])
        total_overdue = _safe_float(row["Overdue"])

        row_dict = dict(row.to_dict())

        groups.append({
            "group_name": customer_name,
            "data_rows": [row_dict],
            "total_balance": total_balance,
            "total_overdue": total_overdue,
            "to_emails": to_emails,
            "cc_emails": cc_emails,
        })

    return groups


# ── PDF attachment matching (adapted from main_window.py's exact-filename
#    lookup against a chosen folder: "{Customer Name}.pdf" -> uploaded files
#    matched by stem instead) ────────────────────────────────────────────


def build_pdf_index(pdf_files: list[tuple[str, str]]) -> dict[str, str]:
    """pdf_files: list of (original_filename, saved_path). Returns
    {casefolded stem: saved_path} for later lookup by customer name."""
    index: dict[str, str] = {}
    for filename, path in pdf_files:
        stem = Path(filename or "").stem.strip().casefold()
        if stem:
            index[stem] = path
    return index


def match_attachment(customer_name: str, pdf_index: dict[str, str]) -> Optional[str]:
    """Exact-filename match: Path(customer_name).casefold() against the
    uploaded PDF's Path(filename).stem.casefold() — mirrors the desktop
    app's "{Customer Name}.pdf" lookup."""
    return pdf_index.get(str(customer_name).strip().casefold())


# ── Email validation (verbatim from utils/validators.py) ───────────────────

COMMON_TYPOS = {
    "gmial.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.con": "gmail.com",
    "gnail.com": "gmail.com",
    "gmaill.com": "gmail.com",
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "yahoo.co": "yahoo.com",
    "yahoo.con": "yahoo.com",
    "hotmal.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "hotmail.con": "hotmail.com",
    "outloo.com": "outlook.com",
    "outlok.com": "outlook.com",
    "outlook.co": "outlook.com",
    "rediffmal.com": "rediffmail.com",
    "redifmail.com": "rediffmail.com",
}

# Cache for MX lookups to avoid repeated DNS queries. Shared across
# concurrent job threads, so guarded by a lock - without it, two threads
# racing on the same not-yet-cached domain would each do a redundant DNS
# lookup (harmless beyond the wasted lookup, since dict writes are atomic
# per-key in CPython, but the lock makes that guarantee explicit rather
# than incidental).
_mx_cache: dict[str, bool] = {}
_mx_cache_lock = threading.Lock()


def validate_email_address(email: str) -> dict:
    """Validate a single email address.
    Returns dict with keys: valid (bool), warning (str|None), suggestion (str|None)

    Checks:
    1. Syntax validation (via email_validator)
    2. Domain typo detection (against known typos)
    3. MX record lookup (does the domain accept mail?)
    """
    result = {"valid": True, "warning": None, "suggestion": None}

    email = email.strip().lower()
    if not email or email in ("-", "nan"):
        result["valid"] = False
        result["warning"] = "Empty email address"
        return result

    # 1. Syntax check
    try:
        validated = validate_email(email, check_deliverability=False)
        email = validated.normalized
    except EmailNotValidError as e:
        result["valid"] = False
        result["warning"] = f"Invalid syntax: {str(e)}"
        return result

    domain = email.split("@")[1]

    # 2. Known typo check
    if domain in COMMON_TYPOS:
        correct = COMMON_TYPOS[domain]
        username = email.split("@")[0]
        result["warning"] = f"Possible typo: '{domain}' → did you mean '{correct}'?"
        result["suggestion"] = f"{username}@{correct}"
        return result

    # 3. MX record check (with cache)
    if domain not in _mx_cache:
        with _mx_cache_lock:
            if domain not in _mx_cache:  # re-check inside the lock
                try:
                    answers = dns.resolver.resolve(domain, "MX", lifetime=5)
                    _mx_cache[domain] = len(answers) > 0
                except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
                    _mx_cache[domain] = False
                except (dns.resolver.NoAnswer, dns.resolver.LifetimeTimeout, Exception):
                    # DNS timeout or no answer — don't flag, just skip
                    _mx_cache[domain] = True  # Give benefit of doubt

    if not _mx_cache[domain]:
        result["valid"] = False
        result["warning"] = f"Domain '{domain}' does not accept email (no MX records)"
        return result

    return result


def validate_email_list(emails_str: str) -> list[dict]:
    """Validate a comma-separated list of emails.
    Returns list of dicts: [{"email": str, "valid": bool, "warning": str|None, "suggestion": str|None}]
    """
    results = []
    if not emails_str or emails_str.strip() in ("-", "", "nan"):
        return results

    for email in emails_str.split(","):
        email = email.strip()
        if not email:
            continue
        check = validate_email_address(email)
        check["email"] = email
        results.append(check)

    return results


# ── Currency formatting (verbatim from app/utils/formatters.py) ───────────

def format_indian_currency(value) -> str:
    """Formats a number into Indian currency format (e.g., 1,00,000).
    Handles integers, floats, and strings that look like numbers.
    Returns '-' for 0 or invalid inputs."""
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", "")

        n = float(value)
        if n == 0:
            return "-"

        # Preserve decimals only if non-zero.
        is_negative = n < 0
        n = abs(n)

        s = "{:.2f}".format(n)
        whole, frac = s.split(".")

        # Indian formatting logic for 'whole' part: 1234567 -> 12,34,567
        last_three = whole[-3:]
        rest = whole[:-3]

        if rest:
            rest_rev = rest[::-1]
            rest_formatted = ",".join([rest_rev[i:i + 2] for i in range(0, len(rest_rev), 2)])
            whole_formatted = rest_formatted[::-1] + "," + last_three
        else:
            whole_formatted = last_three

        final = whole_formatted
        if int(frac) > 0:
            final += "." + frac

        if is_negative:
            final = "-" + final

        return final

    except (ValueError, TypeError):
        return str(value) if value else "-"


# ── Mail subject / body building (verbatim from app/mail/mail_builder.py,
#    with app/core/constants.py's MAIL_SUBJECT_TEMPLATE inlined) —
#    real dunning wording sent to real Ultrafine customers, do not reword
#    without the business explicitly asking for new wording. ────────────────

MAIL_SUBJECT_TEMPLATE = (
    "Payment Reminder - {customer_name} - Total Outstanding Rs.{balance_due} - "
    "Total Overdue Rs.{overdue_amount} as on {date}"
)


def build_subject(
    customer_name: str,
    balance_due: float,
    overdue_amount: float,
    as_on_date: Optional[str] = None,
) -> str:
    # Use provided date or fallback to today (matches the desktop app's
    # literal '%dth %b %Y' fallback — the "th" is a fixed literal, not a
    # correct ordinal suffix; the router always supplies as_on_date so this
    # branch is effectively unreachable in normal use, kept only for parity).
    date_str = as_on_date if as_on_date else pd.Timestamp.now().strftime('%dth %b %Y')

    return MAIL_SUBJECT_TEMPLATE.format(
        customer_name=customer_name,  # Group Name
        balance_due=f"{format_indian_currency(balance_due)} Lakhs",
        overdue_amount=f"{format_indian_currency(overdue_amount)} Lakhs",
        date=date_str,
    )


def build_mail_body(group_data: dict, signature: str, as_on_date: str) -> str:
    """group_data: the dict shape returned by merge_and_group_data (group_name,
    data_rows, total_balance, total_overdue, to_emails, cc_emails).
    as_on_date: string provided by the user (e.g. "22-Aug-2026")."""

    rows = group_data["data_rows"]
    total_balance = group_data["total_balance"]
    total_overdue = group_data["total_overdue"]

    def format_val(val):
        return format_indian_currency(val)

    def td(val, align="left", nowrap=False):
        style = f'border: 1px solid #4F81BD; padding: 5px; text-align: {align}; font-family: Calibri, sans-serif; font-size: 11pt;'
        if nowrap:
            style += " white-space: nowrap;"
        return f'<td style="{style}">{val}</td>'

    header_style = 'background-color: #4472C4; color: white; border: 1px solid #4F81BD; padding: 5px; font-weight: bold; text-align: center; font-family: Calibri, sans-serif; font-size: 11pt;'

    formatted_signature = signature.replace("\n", "<br>")

    # Initialize Totals
    totals = {
        "Overdue": 0.0,
        "Net O/s": 0.0,
        "Unapplied Amt": 0.0,
        "Accounted O/s": 0.0,
        "0-30": 0.0,
        "31-60": 0.0,
        "61-90": 0.0,
        "91-120": 0.0,
        "121-150": 0.0,
        "151-180": 0.0,
        "181-360": 0.0,
        "361-999999": 0.0,
    }

    credit_days_set = set()

    # Build Table Rows
    table_rows_html = ""
    for row in rows:
        def get_val(key):
            v = row.get(key, 0)
            if v is None:
                return 0
            try:
                if pd.isna(v):
                    return 0
            except (TypeError, ValueError):
                pass
            if isinstance(v, str) and v.strip() in ("-", "", "nan", "NaN"):
                return 0
            return v

        def find_col(aliases):
            for a in aliases:
                if a in row:
                    v = row[a]
                    if v is None:
                        return 0
                    try:
                        if pd.isna(v):
                            return 0
                    except (TypeError, ValueError):
                        pass
                    return v
            return 0

        credit_days_set.add(str(get_val('Credit Days')))

        # Accumulate Totals
        totals["Overdue"] += float(get_val("Overdue"))
        totals["Net O/s"] += float(get_val("Net O/s"))
        totals["Unapplied Amt"] += float(get_val("Unapplied Amt"))
        totals["Accounted O/s"] += float(get_val("Accounted O/s"))

        totals["0-30"] += float(find_col(['0-30']))
        totals["31-60"] += float(find_col(['31-60']))
        totals["61-90"] += float(find_col(['61-90']))
        totals["91-120"] += float(find_col(['91-120']))
        totals["121-150"] += float(find_col(['121-150']))
        totals["151-180"] += float(find_col(['151-180']))
        totals["181-360"] += float(find_col(['181-360']))
        totals["361-999999"] += float(find_col(['361-999999']))

        table_rows_html += "<tr>"
        table_rows_html += td(str(row.get('Customer Name', '')), 'left')
        table_rows_html += td(format_val(get_val('Credit Days')), 'center')
        table_rows_html += td(format_val(get_val('Overdue')), 'right')
        table_rows_html += td(format_val(get_val('Net O/s')), 'right')
        table_rows_html += td(format_val(get_val('Unapplied Amt')), 'right')
        table_rows_html += td(format_val(get_val('Accounted O/s')), 'right')
        table_rows_html += td(format_val(find_col(['0-30'])), 'right')
        table_rows_html += td(format_val(find_col(['31-60'])), 'right')
        table_rows_html += td(format_val(find_col(['61-90'])), 'right')
        table_rows_html += td(format_val(find_col(['91-120'])), 'right')
        table_rows_html += td(format_val(find_col(['121-150'])), 'right')
        table_rows_html += td(format_val(find_col(['151-180'])), 'right')
        table_rows_html += td(format_val(find_col(['181-360'])), 'right')
        table_rows_html += td(format_val(find_col(['361-999999'])), 'right')
        table_rows_html += "</tr>"

    # Add Totals Row if multiple rows
    if len(rows) > 1:
        t_style = 'border: 1px solid #4F81BD; padding: 5px; text-align: right; font-family: Calibri, sans-serif; font-size: 11pt; font-weight: bold; background-color: #f2f2f2;'

        def td_total(val, align="right", colspan=1):
            s = t_style + f" text-align: {align};"
            c = f' colspan="{colspan}"' if colspan > 1 else ""
            return f'<td style="{s}"{c}>{val}</td>'

        table_rows_html += "<tr>"
        table_rows_html += td_total("Total", "center", colspan=1)
        table_rows_html += td_total("-", "center")
        table_rows_html += td_total(format_val(totals["Overdue"]))
        table_rows_html += td_total(format_val(totals["Net O/s"]))
        table_rows_html += td_total(format_val(totals["Unapplied Amt"]))
        table_rows_html += td_total(format_val(totals["Accounted O/s"]))
        table_rows_html += td_total(format_val(totals["0-30"]))
        table_rows_html += td_total(format_val(totals["31-60"]))
        table_rows_html += td_total(format_val(totals["61-90"]))
        table_rows_html += td_total(format_val(totals["91-120"]))
        table_rows_html += td_total(format_val(totals["121-150"]))
        table_rows_html += td_total(format_val(totals["151-180"]))
        table_rows_html += td_total(format_val(totals["181-360"]))
        table_rows_html += td_total(format_val(totals["361-999999"]))
        table_rows_html += "</tr>"

    # Format Credit Days Display: "60" or "30, 60"
    credit_days_str = ", ".join(sorted(list(credit_days_set)))
    if not credit_days_str or credit_days_str == "0":
        credit_days_str = "60"  # Fallback if missing

    html = f"""
    <html>
    <body style="font-family: Calibri, sans-serif; font-size: 12pt; color: black;">
        <p>Dear Sir/Madam,</p>

        <p><strong>Greetings from Ultrafine Mineral &amp; Admixtures Pvt. Ltd.!</strong></p>

        <p>As on date, the Net Outstanding Amount is <strong>&#8377;{format_val(total_balance)} Lakhs</strong>,
        out of which <strong>&#8377;{format_val(total_overdue)} Lakhs</strong> is Overdue as on <strong>{as_on_date}</strong>,
        with balances pending beyond the agreed Credit Period of <strong>{credit_days_str} days</strong>.
        We request you to kindly arrange for the release of the due amount at the earliest.</p>

        <br>
        <table style="border-collapse: collapse; width: 100%;">
            <tr>
                <th style="{header_style}">Customer Name</th>
                <th style="{header_style}">Credit<br>Days</th>
                <th style="{header_style}">Overdue</th>
                <th style="{header_style}">Net O/S</th>
                <th style="{header_style}">Unapplied<br>Amt</th>
                <th style="{header_style}">Accounted<br>O/S</th>

                <th style="{header_style} white-space: nowrap;">0 to<br>30</th>
                <th style="{header_style} white-space: nowrap;">31 to<br>60</th>
                <th style="{header_style} white-space: nowrap;">61 to<br>90</th>
                <th style="{header_style} white-space: nowrap;">91 to<br>120</th>
                <th style="{header_style} white-space: nowrap;">121 to<br>150</th>
                <th style="{header_style} white-space: nowrap;">151 to<br>180</th>
                <th style="{header_style} white-space: nowrap;">181 to<br>360</th>
                <th style="{header_style}">+360</th>
            </tr>
            {table_rows_html}
        </table>
        (Value in Lakhs)
        <br>
        <p>In case the payment has already been processed, please share the payment details for our reference.</p>

        <p>Your prompt support in closing the above dues will be highly appreciated.</p>

        <p>Thank you for your cooperation.</p>
        <br>
        <div>
        {formatted_signature}
        </div>
    </body>
    </html>
    """
    return html


# ── Send-plan assembly + bulk send (new — ports app/mail/mail_sender.py's
#    send loop and mirrors ultrafine_balance_confirmation/processor.py's
#    build_send_plan/send_bulk_mails, adapted to this tool's group_name /
#    total_balance / total_overdue shape). Unlike that sibling, recipient
#    merging with the saved Customer -> Email mapping already happened
#    inside merge_and_group_data above — groups arrive here with to_emails /
#    cc_emails already resolved to their final (possibly still blank) comma
#    string, so this function only needs to split, validate and decide
#    skip/sendable, then build the exact subject/body every row would get.
# ────────────────────────────────────────────────────────────────────────


def split_emails(value) -> list[str]:
    """Comma/semicolon-separated string -> deduped, trimmed list. Mirrors
    ultrafine_balance_confirmation/processor.py's split_emails, adapted to
    this module's is_blank_email_value blank-detection."""
    raw = "" if value is None else str(value)
    if is_blank_email_value(raw):
        return []
    items = [item.strip() for item in re.split(r"[,;]", raw) if item.strip()]
    return list(dict.fromkeys(items))


def _is_valid_email_syntax(address: str) -> bool:
    try:
        validate_email(address, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def build_send_plan(
    groups: list[dict],
    signature: str,
    as_on_date: str,
    pdf_index: dict[str, str],
    progress_cb=None,
) -> list[dict]:
    """Turn merge_and_group_data's per-customer groups into the full send
    plan the mandatory human preview step shows: parsed/validated To/CC
    recipients, matching PDF attachment, skip reason, and the exact
    subject/body build_subject/build_mail_body would produce for real.
    Nothing here sends anything.
    """
    total = len(groups) or 1
    plan: list[dict] = []
    for index, group in enumerate(groups):
        customer_name = group["group_name"]
        total_balance = group["total_balance"]
        total_overdue = group["total_overdue"]

        to_emails = split_emails(group.get("to_emails", ""))
        cc_emails = split_emails(group.get("cc_emails", ""))

        skip_reason = None
        if not to_emails and not cc_emails:
            skip_reason = "No To or CC recipient (blank in upload and no saved mapping)"
        else:
            invalid = [addr for addr in to_emails + cc_emails if not _is_valid_email_syntax(addr)]
            if invalid:
                skip_reason = f"Invalid email address: {invalid[0]}"

        pdf_path = match_attachment(customer_name, pdf_index)
        subject = build_subject(customer_name, total_balance, total_overdue, as_on_date)
        body = build_mail_body(group, signature, as_on_date)

        plan.append({
            "group_name": customer_name,
            "total_balance": total_balance,
            "total_overdue": total_overdue,
            "to_emails": to_emails,
            "cc_emails": cc_emails,
            "pdf_attachment_path": pdf_path,
            "pdf_filename": os.path.basename(pdf_path) if pdf_path else None,
            "pdf_attached": bool(pdf_path),
            "skip_reason": skip_reason,
            "subject": subject,
            "body": body,
        })
        if progress_cb:
            progress_cb(
                min(0.95, 0.05 + 0.90 * (index + 1) / total),
                f"Built preview {index + 1} of {len(groups)}",
            )
    return plan


def _send_single_mail(
    server: smtplib.SMTP,
    from_email: str,
    to_emails: list[str],
    cc_emails: list[str],
    subject: str,
    html_body: str,
    attachment_path: Optional[str] = None,
) -> None:
    for address in to_emails + cc_emails:
        validate_email(address, check_deliverability=False)
    message = EmailMessage()
    message["From"] = from_email
    if to_emails:
        message["To"] = ", ".join(to_emails)
    if cc_emails:
        message["Cc"] = ", ".join(cc_emails)
    message["Subject"] = subject
    message.set_content("Please view this message in an HTML-capable email client.")
    message.add_alternative(html_body, subtype="html")
    if attachment_path:
        content_type, encoding = mimetypes.guess_type(attachment_path)
        if not content_type or encoding:
            content_type = "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        data = read_attachment_bytes(attachment_path)
        message.add_attachment(
            data, maintype=maintype, subtype=subtype,
            filename=os.path.basename(attachment_path),
        )
    server.send_message(message)


def send_bulk_mails(
    from_email: str,
    app_password: str,
    groups: list[dict],
    progress_cb=None,
) -> list[dict]:
    """Send every non-skipped group's already-built subject/body (from
    build_send_plan — nothing is rebuilt or reparsed here), one SMTP
    connection reused for the whole batch. Mirrors
    ultrafine_balance_confirmation/processor.py's send_bulk_mails. Returns
    a per-group report: {group_name, total_balance, total_overdue,
    to_emails, cc_emails, pdf_attached, status, detail} where status is
    "sent" | "skipped" | "failed".
    """
    report: list[dict] = []
    sendable = []
    for group in groups:
        if group.get("skip_reason"):
            report.append({
                "group_name": group["group_name"],
                "total_balance": group["total_balance"],
                "total_overdue": group["total_overdue"],
                "to_emails": group["to_emails"],
                "cc_emails": group["cc_emails"],
                "pdf_attached": group["pdf_attached"],
                "status": "skipped",
                "detail": group["skip_reason"],
            })
        else:
            sendable.append(group)

    total = len(sendable)
    if total == 0:
        return report

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(from_email.strip(), app_password.strip())
        for index, group in enumerate(sendable, start=1):
            try:
                _send_single_mail(
                    server,
                    from_email,
                    group["to_emails"],
                    group["cc_emails"],
                    group["subject"],
                    group["body"],
                    group.get("pdf_attachment_path"),
                )
                status, detail = "sent", "Sent successfully"
            except Exception as exc:  # noqa: BLE001 - per-recipient send boundary
                status, detail = "failed", str(exc)
            report.append({
                "group_name": group["group_name"],
                "total_balance": group["total_balance"],
                "total_overdue": group["total_overdue"],
                "to_emails": group["to_emails"],
                "cc_emails": group["cc_emails"],
                "pdf_attached": group["pdf_attached"],
                "status": status,
                "detail": detail,
            })
            if progress_cb:
                progress_cb(min(0.99, index / total), f"Sent {index} of {total}")
    return report
