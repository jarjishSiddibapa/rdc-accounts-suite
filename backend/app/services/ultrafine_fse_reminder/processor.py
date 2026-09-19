"""Parses the credit-control team's "Coll vs Target" tracker sheet and builds
both kinds of reminder email: one per FSE (Field Sales Executive), and one
broadcast combining every FSE for management.

The tracker workbook (see the real "NEW FSE TRACKER <Month><Year>.xlsx")
carries dozens of unrelated sheets - only the "Coll vs Target" sheet matters
here. That sheet's own header text drifts every month (dates embedded in
column headers, extra decoy weekly-slice columns inserted/removed), so
column detection below is driven entirely by header CONTENT, never by fixed
column letters - see detect_columns' docstring for the exact rule that
distinguishes the real "Target/Received/Short Fall" triplet from decoy
columns reusing similar wording.
"""

import html
import logging
import math
import re
import zipfile
from typing import Optional

import pandas as pd
from email_validator import EmailNotValidError, validate_email

from app.jobs import JobUserError

logger = logging.getLogger(__name__)

SHEET_NAME = "Coll vs Target"

_TITLE_RE = re.compile(r"^\s*Collection\s+VS\s+Target\s+Summary", re.IGNORECASE)
_FSE_HEADER_RE = re.compile(r"^\s*FSE\s*$", re.IGNORECASE)
_PARTY_HEADER_RE = re.compile(r"party.?s?\s*name", re.IGNORECASE)
_TARGET_HEADER_RE = re.compile(r"considering dues upto", re.IGNORECASE)
_RECEIVED_HEADER_RE = re.compile(r"received\s+as\s+on", re.IGNORECASE)
_TOTAL_LABEL_RE = re.compile(r"total\s*$", re.IGNORECASE)
_GRAND_TOTAL_RE = re.compile(r"^\s*grand\s+total\s*$", re.IGNORECASE)
_AS_ON_RE = re.compile(r"received\s+as\s+on\s+(.+)$", re.IGNORECASE)


def _norm(value) -> str:
    if value is None:
        return ""
    return str(value).replace("’", "'").strip()


def _safe_float(value) -> float:
    try:
        f = float(value)
        if math.isnan(f):
            return 0.0
        return f
    except (ValueError, TypeError):
        return 0.0


def find_title(ws, max_scan_rows: int = 6) -> Optional[str]:
    for row in ws.iter_rows(min_row=1, max_row=max_scan_rows):
        for cell in row:
            text = _norm(cell.value)
            if text and _TITLE_RE.match(text):
                return text
    return None


def find_header_row(ws, max_scan_rows: int = 10) -> Optional[int]:
    for row in ws.iter_rows(min_row=1, max_row=max_scan_rows):
        for cell in row:
            if _FSE_HEADER_RE.match(_norm(cell.value)):
                return cell.row
    return None


class ColumnLayout:
    def __init__(self, fse_col: int, party_col: int, target_col: int, received_col: int,
                 shortfall_col: int, target_header: str, received_header: str):
        self.fse_col = fse_col
        self.party_col = party_col
        self.target_col = target_col
        self.received_col = received_col
        self.shortfall_col = shortfall_col
        self.target_header = target_header
        self.received_header = received_header


def detect_columns(ws, header_row: int) -> ColumnLayout:
    """Identify the columns we need purely from header CONTENT.

    fse_col: first column headed exactly "FSE".
    party_col: first column matching "Party's Name" (apostrophe/spacing tolerant).
    target/received/shortfall: the sheet repeats several "weekly slice"
    Target/Received/Short Fall triplets (different wording, e.g. "12-18-Dec +
    Previous week ShortFall Coll Target Dec25") plus one stale, #REF!-broken
    decoy month-end Target column - the ONLY column we want is the one whose
    header contains "considering dues upto" AND whose immediately next
    column's header contains "received as on" (a real triplet; the stale
    decoy is followed by another "considering dues upto" column, not a
    "received as on" one, so it's correctly skipped). Short Fall is simply
    the column right after the matched Received column - verified true for
    every triplet in the real file, including the decoy-adjacent ones.
    """
    max_col = ws.max_column
    headers = {col: _norm(ws.cell(header_row, col).value) for col in range(1, max_col + 1)}

    fse_col = next((col for col, text in headers.items() if _FSE_HEADER_RE.match(text)), None)
    party_col = next((col for col, text in headers.items() if _PARTY_HEADER_RE.search(text)), None)

    target_col = received_col = shortfall_col = None
    for col in range(1, max_col):
        if _TARGET_HEADER_RE.search(headers.get(col, "")) and _RECEIVED_HEADER_RE.search(headers.get(col + 1, "")):
            target_col, received_col, shortfall_col = col, col + 1, col + 2
            break

    missing = [
        name for name, value in (
            ("FSE", fse_col), ("Party's Name", party_col),
            ("Collection Target considering dues upto...", target_col),
            ("Coll Received as on...", received_col),
        ) if value is None
    ]
    if missing:
        seen_headers = ", ".join(text for text in headers.values() if text)
        raise JobUserError(
            f"Could not find the expected column(s) [{', '.join(missing)}] on the "
            f"'{SHEET_NAME}' sheet's header row. Headers found: {seen_headers}"
        )

    return ColumnLayout(
        fse_col=fse_col, party_col=party_col,
        target_col=target_col, received_col=received_col, shortfall_col=shortfall_col,
        target_header=headers[target_col], received_header=headers[received_col],
    )


def parse_as_on_date(received_header: str) -> tuple[str, str]:
    """Return (raw, long) - e.g. ("16-Sep-26", "16-Sep-2026"). Falls back to
    using the raw text for both if it can't be parsed as a date - a cosmetic
    date-format miss should never fail the whole job."""
    match = _AS_ON_RE.search(received_header)
    raw = match.group(1).strip() if match else received_header.strip()
    try:
        parsed = pd.to_datetime(raw, dayfirst=True, errors="raise")
        return raw, parsed.strftime("%d-%b-%Y")
    except Exception:
        return raw, raw


def apply_as_on_override(parsed: dict, as_on_date: str) -> None:
    """Overrides parsed's as_on_raw/as_on_long (and the received column's own
    header text) with a user-picked date, so the subject/body/table agree
    even when it differs from the file's own "received as on" header - e.g.
    the app is being used a couple of days after the data was pulled."""
    picked = pd.to_datetime(as_on_date)
    raw = picked.strftime("%d-%b-%y")
    match = _AS_ON_RE.search(parsed["received_header"])
    if match:
        parsed["received_header"] = parsed["received_header"][: match.start(1)] + raw
    parsed["as_on_raw"] = raw
    parsed["as_on_long"] = picked.strftime("%d-%b-%Y")


def read_coll_vs_target(path: str) -> dict:
    """Parse the tracker workbook's 'Coll vs Target' sheet into
    {title, target_header, received_header, as_on_raw, as_on_long, rows}
    where rows is a list of {fse, party, target, received, shortfall} detail
    rows (subtotal/"X Total" rows and the trailing junk below "Grand Total"
    are excluded - see detect_columns/module docstring)."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except zipfile.BadZipFile as exc:
        logger.warning("FSE reminder upload is not a valid Excel file: %s", exc)
        raise JobUserError(
            "This doesn't look like a valid Excel file - please check you uploaded the right file."
        ) from exc
    except Exception as exc:
        logger.warning("FSE reminder upload failed to open: %s", exc)
        raise JobUserError(
            "Could not open the uploaded file as an Excel workbook. Please check it isn't corrupted "
            "and try again."
        ) from exc

    try:
        if SHEET_NAME not in wb.sheetnames:
            raise JobUserError(
                f"Sheet '{SHEET_NAME}' not found in the uploaded file. "
                f"Sheets found: {', '.join(wb.sheetnames)}"
            )
        ws = wb[SHEET_NAME]

        title = find_title(ws) or f"Collection VS Target Summary"
        header_row = find_header_row(ws)
        if header_row is None:
            raise JobUserError(f"Could not find the header row (a cell reading exactly 'FSE') on the '{SHEET_NAME}' sheet.")

        layout = detect_columns(ws, header_row)
        as_on_raw, as_on_long = parse_as_on_date(layout.received_header)

        rows: list[dict] = []
        for row in ws.iter_rows(min_row=header_row + 1, max_row=ws.max_row):
            fse = _norm(row[layout.fse_col - 1].value)
            party = _norm(row[layout.party_col - 1].value)
            if _GRAND_TOTAL_RE.match(party):
                break
            if not fse or not party or _TOTAL_LABEL_RE.search(party):
                continue
            rows.append({
                "fse": fse,
                "party": party,
                "target": _safe_float(row[layout.target_col - 1].value),
                "received": _safe_float(row[layout.received_col - 1].value),
                "shortfall": _safe_float(row[layout.shortfall_col - 1].value),
            })

        if not rows:
            raise JobUserError(f"No data rows found on the '{SHEET_NAME}' sheet.")

        return {
            "title": title,
            "target_header": layout.target_header,
            "received_header": layout.received_header,
            "as_on_raw": as_on_raw,
            "as_on_long": as_on_long,
            "rows": rows,
        }
    finally:
        wb.close()


def group_by_fse(rows: list[dict]) -> "dict[str, dict]":
    """{fse: {rows: [...], total_target, total_received, total_shortfall}},
    preserving first-seen FSE order and each FSE's own row order."""
    groups: dict[str, dict] = {}
    for row in rows:
        group = groups.setdefault(row["fse"], {"rows": [], "total_target": 0.0, "total_received": 0.0, "total_shortfall": 0.0})
        group["rows"].append(row)
        group["total_target"] += row["target"]
        group["total_received"] += row["received"]
        group["total_shortfall"] += row["shortfall"]
    return groups


# ── Recipients ──────────────────────────────────────────────────────────────

INDIVIDUAL_CC = [
    "Devanand Singh <devanand.singh@ultrafine.in>",
    "Umesh Gawade - Accounts <umesh.gawade@rdc.in>",
    "Harish Ludhani <harish.ludhani@ultrafine.in>",
    "Rakesh Desai <rakesh.desai@ultrafine.in>",
]

BROADCAST_CC = [
    "Devanand Singh <devanand.singh@ultrafine.in>",
    "Umesh Gawade - Accounts <umesh.gawade@rdc.in>",
    "Abhay Singh <abhay.singh@ultrafine.in>",
    "Rakesh Desai <rakesh.desai@ultrafine.in>",
    "Ashutosh Jha <ashutosh.jha@ultrafine.in>",
    "Ghoghari Jaysukh <ghoghari.jaysukh@ultrafine.in>",
    "Hitanshi Jain <hitanshi.jain@rdc.in>",
]

_ADDR_RE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")


def extract_address(value: str) -> str:
    """"Name <email@x.com>" -> "email@x.com" (lowercased); a bare address is
    returned lowercased as-is."""
    match = _ADDR_RE.search(value or "")
    return (match.group(1) if match else (value or "")).strip().lower()


def dedupe_cc_against_to(to_list: list[str], cc_list: list[str]) -> list[str]:
    """Drop any Cc entry whose address already appears in To (case-
    insensitive) - covers an FSE who is also a standing Cc on their own
    reminder, or (for the broadcast) a salesperson who happens to also be
    one of the fixed management Cc addresses."""
    to_addresses = {extract_address(addr) for addr in to_list}
    return [addr for addr in cc_list if extract_address(addr) not in to_addresses]


def is_valid_email_syntax(address: str) -> bool:
    try:
        validate_email(extract_address(address), check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


# ── HTML table / mail body ───────────────────────────────────────────────────

def _fmt(value: float) -> str:
    return f"{value:.2f}"


# FSE and Party's Name get most of the table's width so a name almost never
# wraps; the three figure columns are deliberately narrow so their longer
# headers (e.g. "Collection Target considering dues upto 30-Sep-26") wrap
# onto a few short lines instead of forcing the whole table wide.
_COL_WIDTHS = {"fse": "14%", "party": "34%", "target": "18%", "received": "18%", "shortfall": "16%"}


def _td(value, align: str = "right", bold: bool = False, bg: Optional[str] = None,
        width: Optional[str] = None) -> str:
    style = (
        f"border:1px solid #4472C4;padding:5px 10px;text-align:{align};"
        "font-family:Calibri,Arial,sans-serif;font-size:11pt;"
    )
    if bold:
        style += "font-weight:bold;"
    if bg:
        style += f"background-color:{bg};"
    if width:
        style += f"width:{width};"
    width_attr = f' width="{width}"' if width else ""
    return f'<td style="{style}"{width_attr}>{value}</td>'


def _header_cell(text: str, bg: str = "#F4B183", width: Optional[str] = None) -> str:
    style = (
        f"border:1px solid #4472C4;padding:6px 10px;background-color:{bg};"
        "color:#1a1a1a;font-weight:bold;text-align:center;"
        "font-family:Calibri,Arial,sans-serif;font-size:11pt;"
    )
    if width:
        style += f"width:{width};"
    width_attr = f' width="{width}"' if width else ""
    return f'<th style="{style}"{width_attr}>{html.escape(text)}</th>'


def build_table_html(
    title: str,
    target_header: str,
    received_header: str,
    fse_blocks: list[tuple[str, dict]],
    grand_total: Optional[dict] = None,
) -> str:
    """fse_blocks: [(fse_name, group_dict), ...] in the order they should
    appear. `grand_total` (only passed for the broadcast mail) appends one
    final bold Grand Total row summing every block."""
    body_bg = "#DCE6F1"
    w = _COL_WIDTHS
    rows_html = []
    for fse_name, group in fse_blocks:
        for row in group["rows"]:
            rows_html.append(
                "<tr>"
                + _td(html.escape(fse_name), "left", bg=body_bg, width=w["fse"])
                + _td(html.escape(row["party"]), "left", bg=body_bg, width=w["party"])
                + _td(_fmt(row["target"]), "right", bg=body_bg, width=w["target"])
                + _td(_fmt(row["received"]), "right", bg=body_bg, width=w["received"])
                + _td(_fmt(row["shortfall"]), "right", bg=body_bg, width=w["shortfall"])
                + "</tr>"
            )
        rows_html.append(
            "<tr>"
            + _td(html.escape(fse_name), "left", bold=True, bg=body_bg, width=w["fse"])
            + _td(f"{html.escape(fse_name)} Total", "left", bold=True, bg=body_bg, width=w["party"])
            + _td(_fmt(group["total_target"]), "right", bold=True, bg=body_bg, width=w["target"])
            + _td(_fmt(group["total_received"]), "right", bold=True, bg=body_bg, width=w["received"])
            + _td(_fmt(group["total_shortfall"]), "right", bold=True, bg=body_bg, width=w["shortfall"])
            + "</tr>"
        )

    if grand_total is not None:
        rows_html.append(
            "<tr>"
            + _td("", "left", bold=True, bg=body_bg, width=w["fse"])
            + _td("Grand Total", "left", bold=True, bg=body_bg, width=w["party"])
            + _td(_fmt(grand_total["total_target"]), "right", bold=True, bg=body_bg, width=w["target"])
            + _td(_fmt(grand_total["total_received"]), "right", bold=True, bg=body_bg, width=w["received"])
            + _td(_fmt(grand_total["total_shortfall"]), "right", bold=True, bg=body_bg, width=w["shortfall"])
            + "</tr>"
        )

    title_style = (
        "border:1px solid #4472C4;padding:6px 10px;background-color:#A9D08E;"
        "color:#1a1a1a;font-weight:bold;text-align:center;"
        "font-family:Calibri,Arial,sans-serif;font-size:12pt;"
    )
    return f"""<table style="border-collapse:collapse;table-layout:fixed;width:100%;" width="100%">
<tr><td colspan="5" style="{title_style}">{html.escape(title)}</td></tr>
<tr>
{_header_cell("FSE", width=w["fse"])}
{_header_cell("Party's Name", width=w["party"])}
{_header_cell(target_header, bg="#FFFF00", width=w["target"])}
{_header_cell(received_header, width=w["received"])}
{_header_cell("Short Fall", width=w["shortfall"])}
</tr>
{''.join(rows_html)}
</table>"""


SUBJECT_TEMPLATE = "Collection Target VS Actual Collection Received as on {as_on_raw}"


def build_subject(as_on_raw: str) -> str:
    return SUBJECT_TEMPLATE.format(as_on_raw=as_on_raw)


def _signature_html(signature: str) -> str:
    if not signature or not signature.strip():
        return ""
    lines = signature.strip().replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return '<p style="margin-top:24px;font-family:Calibri,Arial,sans-serif;font-size:12pt;">' + "<br>".join(html.escape(line) for line in lines) + "</p>"


DEFAULT_ADVISORY_HTML = (
    "<p>It is critical that we prioritize the immediate collection of these outstanding amounts "
    "to ensure timely vendor payments and maintain our operational flow without disruption</p>"
)


def build_individual_body(
    fse_name: str, group: dict, table_html: str, as_on_long: str, signature: str,
    advisory_html: Optional[str] = None,
) -> str:
    advisory = advisory_html if advisory_html is not None else DEFAULT_ADVISORY_HTML
    return f"""<html><body style="font-family:Calibri,Arial,sans-serif;font-size:12pt;color:#1a1a1a;">
<p>Dear Sir/ Ma'am,</p>
<p>Please find the collection v/s target Summary from 1 to {html.escape(as_on_long)}. We have only collected {_fmt(group['total_received'])} Lakh against a target of {_fmt(group['total_target'])} Lakhs</p>
{advisory}
{table_html}
{_signature_html(signature)}
</body></html>"""


def build_broadcast_body(
    grand_total: dict, table_html: str, as_on_long: str, signature: str,
    advisory_html: Optional[str] = None,
) -> str:
    advisory = advisory_html if advisory_html is not None else DEFAULT_ADVISORY_HTML
    return f"""<html><body style="font-family:Calibri,Arial,sans-serif;font-size:12pt;color:#1a1a1a;">
<p>Dear All</p>
<p>Please find the collection v/s target Summary from 1 to {html.escape(as_on_long)}. We have only collected {_fmt(grand_total['total_received'])} Lakh against a target of {_fmt(grand_total['total_target'])} Lakhs</p>
{advisory}
{table_html}
{_signature_html(signature)}
</body></html>"""


# ── Send-plan assembly ───────────────────────────────────────────────────────

def build_send_plan(
    parsed: dict, mapping: dict[str, str], signature: str,
    advisory_html: Optional[str] = None,
) -> dict:
    """parsed: read_coll_vs_target's return value. mapping: fse_name -> email
    (from mapping_store.load_all). advisory_html (shared across every
    individual reminder AND the broadcast) overrides the fixed advisory
    paragraph - editing a single row's own body_html afterward only affects
    that one FSE, since only advisory_html is regenerated for everyone.
    Returns {individual: [...], broadcast: {...}} with every subject/body/
    to/cc already built, ready for the frontend to show and let the user
    edit before send."""
    groups = group_by_fse(parsed["rows"])
    subject = build_subject(parsed["as_on_raw"])

    individual = []
    broadcast_to: list[str] = []
    for fse_name, group in groups.items():
        email = (mapping.get(fse_name) or "").strip()
        missing_email = not email
        to = [] if missing_email else [email]
        cc = [] if missing_email else [extract_address(addr) for addr in dedupe_cc_against_to(to, INDIVIDUAL_CC)]
        if not missing_email:
            broadcast_to.append(email)

        table_html = build_table_html(parsed["title"], parsed["target_header"], parsed["received_header"], [(fse_name, group)])
        individual.append({
            "fse_key": fse_name,
            "fse_name": fse_name,
            "total_target": group["total_target"],
            "total_received": group["total_received"],
            "total_shortfall": group["total_shortfall"],
            "party_count": len(group["rows"]),
            "to": to,
            "cc": cc,
            "missing_email": missing_email,
            "subject": subject,
            "body_html": build_individual_body(
                fse_name, group, table_html, parsed["as_on_long"], signature, advisory_html,
            ),
        })

    grand_total = {
        "total_target": sum(g["total_target"] for g in groups.values()),
        "total_received": sum(g["total_received"] for g in groups.values()),
        "total_shortfall": sum(g["total_shortfall"] for g in groups.values()),
    }
    broadcast_cc = [extract_address(addr) for addr in dedupe_cc_against_to(broadcast_to, BROADCAST_CC)]
    broadcast_table_html = build_table_html(
        parsed["title"], parsed["target_header"], parsed["received_header"],
        list(groups.items()), grand_total=grand_total,
    )
    broadcast = {
        "to": broadcast_to,
        "cc": broadcast_cc,
        "subject": subject,
        "body_html": build_broadcast_body(
            grand_total, broadcast_table_html, parsed["as_on_long"], signature, advisory_html,
        ),
        "total_target": grand_total["total_target"],
        "total_received": grand_total["total_received"],
        "total_shortfall": grand_total["total_shortfall"],
        "fse_count": len(groups),
        "missing_email_count": sum(1 for row in individual if row["missing_email"]),
        # Raw per-party rows + table wording, carried through so /send-broadcast
        # can rebuild an Excel version of this exact table as an attachment
        # without needing the original upload again (see build_broadcast_workbook).
        "table_title": parsed["title"],
        "target_header": parsed["target_header"],
        "received_header": parsed["received_header"],
        "table_rows": parsed["rows"],
    }

    return {"individual": individual, "broadcast": broadcast}


def build_broadcast_workbook(title: str, target_header: str, received_header: str, table_rows: list[dict]) -> bytes:
    """Excel version of exactly what the broadcast email's own HTML table
    shows (same title/header/FSE blocks/Total rows/Grand Total, same color
    scheme) - a take-away reference copy attached for the recipient. Purely
    a display export; unlike the upload template it is never read back into
    this app, so its Total/Grand Total rows are real (computed here, not
    ignored)."""
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    groups = group_by_fse(table_rows)
    grand_total = {
        "total_target": sum(g["total_target"] for g in groups.values()),
        "total_received": sum(g["total_received"] for g in groups.values()),
        "total_shortfall": sum(g["total_shortfall"] for g in groups.values()),
    }

    title_fill = PatternFill("solid", fgColor="A9D08E")
    header_fill = PatternFill("solid", fgColor="F4B183")
    target_fill = PatternFill("solid", fgColor="FFFF00")
    body_fill = PatternFill("solid", fgColor="DCE6F1")
    border = Border(*(Side(style="thin", color="4472C4"),) * 4)
    bold = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ncols = 5

    def style_row(row: int, fill: PatternFill, *, bold_row: bool = False, align: Optional[Alignment] = None) -> None:
        for col in range(1, ncols + 1):
            cell = ws.cell(row, col)
            cell.fill = fill
            cell.border = border
            if bold_row:
                cell.font = bold
            if align:
                cell.alignment = align

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Coll vs Target"

    ws.cell(1, 1).value = title
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    style_row(1, title_fill, bold_row=True, align=center)

    ws.append(["FSE", "Party's Name", target_header, received_header, "Short Fall"])
    style_row(2, header_fill, bold_row=True, align=center)
    ws.cell(2, 3).fill = target_fill

    row_num = 2
    for fse_name, group in groups.items():
        for row in group["rows"]:
            row_num += 1
            ws.append([fse_name, row["party"], row["target"], row["received"], row["shortfall"]])
            style_row(row_num, body_fill)
        row_num += 1
        ws.append([fse_name, f"{fse_name} Total", group["total_target"], group["total_received"], group["total_shortfall"]])
        style_row(row_num, body_fill, bold_row=True)

    row_num += 1
    ws.append([None, "Grand Total", grand_total["total_target"], grand_total["total_received"], grand_total["total_shortfall"]])
    style_row(row_num, body_fill, bold_row=True)

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 14
    ws.freeze_panes = "A3"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
