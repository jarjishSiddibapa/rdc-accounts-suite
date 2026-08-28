"""Ports the desktop app's Excel-reading/validation logic (see
E:\\jarjish-projects\\vishal-sir-balance-confirmation-for-ultrafine\\app\\excel\\excel_reader.py
and app\\utils\\validators.py) plus the "merge uploaded recipients with the
saved Customer -> Email mapping" logic described in this tool's task brief,
and the actual bulk-send loop (ports app\\mail\\mail_sender.py).

Key deviation from the original: the desktop app always required the To/CC
columns to be re-typed into the input workbook on every run. Here, the suite
persists a Customer -> Email mapping (see models.py / mapping_store.py) so a
customer's recipients only need to be entered once. Per row:

  - If the uploaded workbook has a To or CC value for that customer, the
    upload wins for this send (mirrors "give it if you want to override").
  - Otherwise, the customer's saved mapping (if any) is used.
  - If neither has anything, the row is skipped — same "no To or CC
    recipient" skip rule the desktop app used, just with two possible
    sources of recipients instead of one.

The Net O/s + Customer Name columns are always required in the uploaded
workbook, exactly like the original — only the recipient columns became
optional here.

Every row gets its subject/body built (via mail_builder, ported verbatim and
never modified here) regardless of whether it will be skipped, so the
mandatory human preview step can show exactly what *would* be sent if the
row were sendable.
"""

import mimetypes
import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import pandas as pd
from email_validator import EmailNotValidError, validate_email

from . import mail_builder
from app.jobs import JobUserError
from app.services.mailer_shared import read_attachment_bytes

# ── Column aliasing / reading (verbatim from app/excel/excel_reader.py,
#    minus the requirement that To/CC columns be present) ────────────────────

INPUT_ALIASES = {
    "Customer Name": {"customer name", "customer", "party name"},
    "Net O/s": {"net o/s", "net os", "net outstanding", "balance", "balance amount"},
    "To Email IDs": {"to email ids", "to email id", "to email", "email", "email ids"},
    "CC Email IDs": {"cc email ids", "cc email id", "cc email", "cc"},
}

REQUIRED_COLUMNS = ("Customer Name", "Net O/s")
OPTIONAL_COLUMNS = ("To Email IDs", "CC Email IDs")


def _normalise_header(value) -> str:
    return re.sub(r"\s+", " ", str(value).replace("\n", " ").strip()).casefold()


def _rename_aliases(df: pd.DataFrame, aliases: dict) -> pd.DataFrame:
    rename = {}
    for column in df.columns:
        normal = _normalise_header(column)
        for canonical, choices in aliases.items():
            if normal in choices:
                rename[column] = canonical
                break
    return df.rename(columns=rename)


def _ensure_unique_names(df: pd.DataFrame, label: str) -> None:
    keys = df["Customer Name"].astype(str).str.strip().str.casefold()
    duplicates = df.loc[keys.duplicated(keep=False), "Customer Name"].astype(str).unique().tolist()
    if duplicates:
        raise JobUserError(f"Duplicate customer names in {label}: {', '.join(duplicates[:10])}")


def _read_excel(file_path: str) -> pd.DataFrame:
    try:
        return pd.read_excel(file_path, engine="openpyxl")
    except Exception as exc:
        raise JobUserError(f"Could not read the Excel file: {exc}") from exc


def read_input_excel(file_path: str) -> pd.DataFrame:
    """Read the combined balance-and-recipient workbook. Customer Name and
    Net O/s are always required (same as the original); To Email IDs / CC
    Email IDs are optional here — if either column is absent, it's treated
    as blank for every row (the saved mapping can supply recipients)."""
    df = _rename_aliases(_read_excel(file_path), INPUT_ALIASES)
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise JobUserError(f"Missing columns in Input file: {', '.join(missing)}")
    for column in OPTIONAL_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    df = df[list(REQUIRED_COLUMNS) + list(OPTIONAL_COLUMNS)].copy()
    df = df.dropna(how="all")
    df["Customer Name"] = df["Customer Name"].fillna("").astype(str).str.strip()
    df = df[df["Customer Name"] != ""].copy()
    if df.empty:
        raise JobUserError("The Balance Data file contains no customer rows.")
    cleaned = df["Net O/s"].astype(str).str.replace(r"[₹,\s]", "", regex=True).str.replace("Rs.", "", regex=False)
    df["Net O/s"] = pd.to_numeric(cleaned, errors="coerce")
    bad = df[df["Net O/s"].isna()]["Customer Name"].tolist()
    if bad:
        raise JobUserError(f"Invalid Net O/s amount for: {', '.join(bad[:10])}")
    for column in OPTIONAL_COLUMNS:
        df[column] = df[column].fillna("").astype(str).str.strip()
    _ensure_unique_names(df, "Input file")
    return df


def rows_from_input(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "customer_name": str(row["Customer Name"]).strip(),
            "balance": float(row["Net O/s"]),
            "to_emails": str(row["To Email IDs"]).strip(),
            "cc_emails": str(row["CC Email IDs"]).strip(),
        }
        for _, row in df.iterrows()
    ]


# ── Recipient parsing (verbatim from app/utils/validators.py) ────────────────

def split_emails(value) -> list[str]:
    raw = "" if value is None else str(value)
    if raw.strip().lower() in {"", "-", "nan", "none"}:
        return []
    values = [item.strip() for item in re.split(r"[,;]", raw) if item.strip()]
    return list(dict.fromkeys(values))


def _is_valid_email(address: str) -> bool:
    try:
        validate_email(address, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


# ── PDF matching (mirrors main_window.py's _pdf_lookup / _customer_pdf) ─────

def build_pdf_lookup(files: list[tuple[str, str]]) -> dict[str, str]:
    """`files` is a list of (original_filename, saved_path) pairs — the
    caller (router) must pass the ORIGINAL filename here, not the
    uuid-prefixed name save_upload() writes to disk, since matching is by
    <Customer Name>.pdf against the name the user actually uploaded."""
    lookup: dict[str, str] = {}
    for original_name, saved_path in files:
        stem = Path(original_name).stem.strip().casefold()
        suffix = Path(original_name).suffix.casefold()
        if stem and suffix == ".pdf":
            lookup[stem] = saved_path
    return lookup


def _customer_pdf(customer_name: str, pdf_lookup: dict[str, str]) -> Optional[str]:
    return pdf_lookup.get(str(customer_name).strip().casefold())


# ── Merge upload recipients with the saved Customer -> Email mapping ────────

def _normalise_mapping_keys(mapping: dict[str, dict]) -> dict[str, dict]:
    return {str(k).strip().casefold(): v for k, v in mapping.items()}


def build_send_plan(
    rows: list[dict],
    mapping: dict[str, dict],
    signature: str,
    as_on_date,
    pdf_lookup: dict[str, str],
    progress_cb=None,
) -> list[dict]:
    """Produce the full per-customer send plan: recipients (merged with the
    saved mapping), matching PDF, skip reason, and the exact subject/body
    mail_builder would generate. Nothing here sends anything — this is the
    content the mandatory preview step shows the human before confirm-send.
    """
    mapping_by_key = _normalise_mapping_keys(mapping)
    total = len(rows) or 1
    plan: list[dict] = []
    for index, row in enumerate(rows):
        customer_name = row["customer_name"]
        balance = row["balance"]
        upload_to = split_emails(row["to_emails"])
        upload_cc = split_emails(row["cc_emails"])

        if upload_to or upload_cc:
            to_emails, cc_emails, source = upload_to, upload_cc, "upload"
        else:
            saved = mapping_by_key.get(customer_name.strip().casefold(), {})
            to_emails = list(saved.get("to", []))
            cc_emails = list(saved.get("cc", []))
            source = "saved_mapping" if (to_emails or cc_emails) else "none"

        skip_reason = None
        if not to_emails and not cc_emails:
            skip_reason = "No To or CC recipient (blank in upload and no saved mapping)"
        else:
            invalid = [addr for addr in to_emails + cc_emails if not _is_valid_email(addr)]
            if invalid:
                skip_reason = f"Invalid email address: {invalid[0]}"

        pdf_path = _customer_pdf(customer_name, pdf_lookup)
        subject = mail_builder.build_subject(customer_name, as_on_date=as_on_date)
        body = mail_builder.build_mail_body(
            {"customer_name": customer_name, "balance": balance}, signature, as_on_date=as_on_date
        )

        plan.append({
            "customer_name": customer_name,
            "balance": balance,
            "to_emails": to_emails,
            "cc_emails": cc_emails,
            "recipient_source": source,
            "pdf_attachment_path": pdf_path,
            "pdf_filename": os.path.basename(pdf_path) if pdf_path else None,
            "pdf_attached": bool(pdf_path),
            "skip_reason": skip_reason,
            "subject": subject,
            "body": body,
        })
        if progress_cb:
            progress_cb(min(0.95, 0.05 + 0.90 * (index + 1) / total), f"Built preview {index + 1} of {len(rows)}")
    return plan


# ── Bulk send (ports app/mail/mail_sender.py) ────────────────────────────────

def _send_single(server: smtplib.SMTP, from_email: str, to_emails, cc_emails, subject, html_body, attachment_path=None):
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
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(attachment_path))
    server.send_message(message)


def send_bulk_mails(from_email: str, app_password: str, customers: list[dict], progress_cb=None) -> list[dict]:
    """Send every non-skipped customer's already-built subject/body (from
    build_send_plan — nothing is rebuilt or reparsed here), one SMTP
    connection reused for the whole batch. Returns a per-customer report:
    {customer_name, balance, to_emails, cc_emails, pdf_attached, status,
    detail} where status is "sent" | "skipped" | "failed".

    Unlike the desktop app (which opened a fresh SMTP connection per email
    across a small thread pool), this sends sequentially over one
    connection — simpler to report progress for, and Gmail's SMTP rate
    limits make concurrency of limited benefit anyway.
    """
    report: list[dict] = []
    sendable = []
    for customer in customers:
        if customer.get("skip_reason"):
            report.append({
                "customer_name": customer["customer_name"],
                "balance": customer["balance"],
                "to_emails": customer["to_emails"],
                "cc_emails": customer["cc_emails"],
                "pdf_attached": customer["pdf_attached"],
                "status": "skipped",
                "detail": customer["skip_reason"],
            })
        else:
            sendable.append(customer)

    total = len(sendable)
    if total == 0:
        return report

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(from_email.strip(), app_password.strip())
        for index, customer in enumerate(sendable, start=1):
            try:
                _send_single(
                    server,
                    from_email,
                    customer["to_emails"],
                    customer["cc_emails"],
                    customer["subject"],
                    customer["body"],
                    customer.get("pdf_attachment_path"),
                )
                status, detail = "sent", "Sent successfully"
            except Exception as exc:  # noqa: BLE001 - per-recipient send boundary
                status, detail = "failed", str(exc)
            report.append({
                "customer_name": customer["customer_name"],
                "balance": customer["balance"],
                "to_emails": customer["to_emails"],
                "cc_emails": customer["cc_emails"],
                "pdf_attached": customer["pdf_attached"],
                "status": status,
                "detail": detail,
            })
            if progress_cb:
                progress_cb(min(0.99, index / total), f"Sent {index} of {total}")
    return report
