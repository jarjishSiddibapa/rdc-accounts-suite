"""Mail the Ultrafine cashflow of a month.

"Regenerate & mail": the month's workbook is rebuilt from the stored bank lines
and the mappings as they are now, the Summary is laid out as the table in the
mail body (the way the cashflow has always been mailed by hand), and the
workbook is the attachment. Nothing is sent by this module - a person reviews
the preview and confirms (see app.routers.report_mail_routes). It is mailed by
hand only; there is no automatic schedule for it.
"""

from __future__ import annotations

import html
import shutil
import uuid
from collections import Counter
from datetime import datetime
from functools import partial
from pathlib import Path

from app.config import SCRATCH_DIR
from app.database import SessionLocal
from app.jobs import JobUserError
from app.services import report_mail_preview
from app.services import scheduled_report_mail as mail

from .generate import generate_month
from .models import CashflowFile
from .resolver import ISSUE_TEXT, load_maps
from .summary import MonthFigures, month_figures
from .workbook import _ordinal

APP_KEY = "ultrafine-cashflow"

_FONT = "font-family:'Times New Roman',Times,serif;font-size:11pt;"
_CELL = f"border:1px solid #000;padding:1px 4px;{_FONT}"
_HEADER_BG = "#B8CCE4"
_GROUP_BG = "#FFC000"


def _lakhs(value: float | None) -> str:
    """Whole lakhs, negatives in brackets, nothing as a dash - as the cashflow is mailed."""
    number = round(value or 0)
    if number == 0:
        return "-"
    return f"({abs(number):,})" if number < 0 else f"{number:,}"


def _text_cell(text: str, bold: bool = False, align: str = "left", background: str | None = None,
               underline: bool = False) -> str:
    style = _CELL + f"text-align:{align};"
    if bold:
        style += "font-weight:bold;"
    if underline:
        style += "text-decoration:underline;"
    if background:
        style += f"background:{background};"
    return f'<td style="{style}">{html.escape(text) if text else "&nbsp;"}</td>'


def _number_cells(values: list[float], bold: bool = False, background: str | None = None) -> str:
    return "".join(_text_cell(_lakhs(v), bold=bold, align="right", background=background) for v in values)


def summary_table_html(figures: MonthFigures, as_of: datetime) -> str:
    """The Summary sheet as the table of the mail: title, header, one row per CF
    location, an orange total after each group, a blank line and the grand total."""
    month_label = datetime.strptime(figures.month, "%Y-%m").strftime("%B- %Y")
    counts = Counter(row.payable_pct for row in figures.rows)
    payable = f"Payable ({counts.most_common(1)[0][0]:g} % Of Collection)" if counts else "Payable"
    headers = ["Sr.No.", "Location", "Authorized Person", "Op. Balance", f"Collection {month_label}",
               payable, "Actual Paid", "Cl. Balance"]

    title_style = f"border:none;padding:2px 4px;font-weight:bold;{_FONT}"
    lines = [
        '<table style="border-collapse:collapse;" cellspacing="0" cellpadding="0">',
        f'<tr><td colspan="7" style="{title_style}text-align:center;">'
        f'Cash Flow as of {_ordinal(as_of.day)} {as_of:%B, %Y}</td>'
        f'<td style="{title_style}text-align:right;white-space:nowrap;">Amt in Lacs</td></tr>',
        "<tr>" + "".join(
            f'<td style="{_CELL}text-align:center;font-weight:bold;background:{_HEADER_BG};">'
            f"{html.escape(name)}</td>" for name in headers
        ) + "</tr>",
    ]

    def figures_of(rows) -> list[float]:
        return [sum(getattr(r, attr) or 0.0 for r in rows)
                for attr in ("opening", "collection", "payable", "paid", "closing")]

    rows = figures.rows
    members: list = []
    serial = 0
    for index, item in enumerate(rows):
        serial += 1
        lines.append(
            "<tr>"
            + _text_cell(str(serial), align="right")
            + _text_cell(item.name, bold=True)
            + _text_cell(item.authorized_person)
            + _number_cells([item.opening, item.collection, item.payable, item.paid, item.closing])
            + "</tr>"
        )
        if item.group:
            members.append(item)
            following = rows[index + 1].group if index + 1 < len(rows) else None
            if following != item.group:
                lines.append(
                    "<tr>" + _text_cell("", background=_GROUP_BG) + _text_cell("", background=_GROUP_BG)
                    + _text_cell(f"Total {item.group}", bold=True, background=_GROUP_BG)
                    + _number_cells(figures_of(members), bold=True, background=_GROUP_BG) + "</tr>"
                )
                members = []

    lines.append("<tr>" + "".join(_text_cell("") for _ in headers) + "</tr>")
    lines.append(
        "<tr>" + _text_cell("") + _text_cell("Total", bold=True, underline=True) + _text_cell("")
        + _number_cells(figures_of(rows), bold=True) + "</tr>"
    )
    lines.append("</table>")
    return "\n".join(lines)


def _warnings(summary: dict) -> list[dict]:
    """What is unmapped, in the shape the mail panel lists it."""
    warnings: list[dict] = []
    if summary["no_party"]:
        warnings.append({
            "category": "Bank descriptions with no name",
            "items": [f"{entry['sample'][:60]} ({entry['count']})" for entry in summary["no_party"]],
        })
    grouped: dict[str, list[str]] = {}
    for entry in summary["other"]:
        grouped.setdefault(ISSUE_TEXT.get(entry["issue"], entry["issue"]), []).append(
            f"{entry['name'] or entry['sample']} ({entry['count']})")
    warnings.extend({"category": category, "items": items} for category, items in grouped.items())
    return warnings


def build(snapshot: dict, progress_cb=None, cancel_event=None, month: str | None = None) -> mail.MailPayload:
    if not month:
        raise JobUserError("Choose the month to mail.")
    payload = mail.MailPayload(values={"date": "", "date_plain": "", "month": ""})

    db = SessionLocal()
    try:
        if progress_cb:
            progress_cb(0.15, "Regenerating the cashflow...")
        # the same rebuild as "Regenerate": always from the current data and mappings
        outcome = generate_month(db, month, None, allow_unmapped=snapshot["send_when_unmapped"])
        db.commit()
        if outcome["status"] == "no_data":
            payload.hold_reason = "There are no bank lines in this month, so there is no cashflow to mail."
            return payload
        if outcome["status"] == "needs_mapping_fix":
            payload.detail = {"unmapped": _warnings(outcome["unmapped"]), "issue_count": outcome["issue_count"]}
            payload.hold_reason = (
                f"{outcome['issue_count']} bank line(s) are not mapped yet, so the cashflow would be incomplete."
            )
            return payload

        if progress_cb:
            progress_cb(0.7, "Laying out the mail...")
        figures = month_figures(db, month, load_maps(db))
        stored = db.query(CashflowFile).filter(CashflowFile.month == month, CashflowFile.is_deleted == False).first()  # noqa: E712
        if stored is None or not Path(stored.file_path).is_file():
            raise JobUserError("The cashflow workbook could not be found after it was generated.")
        source, file_name = Path(stored.file_path), stored.file_name
    finally:
        db.close()

    as_of = max((a.txn_dt for a in figures.allocations), default=datetime.strptime(month, "%Y-%m"))
    payload.values = {
        "date": f"{_ordinal(as_of.day)} {as_of:%B, %Y}",
        "date_plain": f"{_ordinal(as_of.day)} {as_of:%B %Y}",
        "month": datetime.strptime(month, "%Y-%m").strftime("%B %Y"),
    }
    payload.sections = [("", summary_table_html(figures, as_of))]

    # a copy of its own, so the preview and the send do not depend on the month's stored file
    work_dir = SCRATCH_DIR / f"mail-cashflow-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    attachment = work_dir / file_name
    shutil.copyfile(source, attachment)
    payload.downloads = [("Cashflow workbook", str(attachment))]
    payload.attachment_groups = [[str(attachment)]]
    payload.detail = {
        "month": month,
        "as_of": as_of.date().isoformat(),
        "version": outcome.get("version"),
        "unmapped_lines": outcome.get("unmapped_count", 0),
        "attachment": file_name,
    }
    if outcome.get("unmapped_count"):
        payload.notes.append(
            f"{outcome['unmapped_count']} bank line(s) are not mapped yet. They are highlighted in the "
            "workbook (Remark column) and are not part of the table."
        )
    if outcome.get("earlier_issue_count"):
        payload.notes.append(
            f"{outcome['earlier_issue_count']} unmapped line(s) in earlier months affect the opening balances."
        )
    return payload


def run_preview_job(user_id: int, to: list, cc: list, subject: str, intro: str,
                    allow_unmapped: bool = False, month: str | None = None,
                    progress_cb=None, cancel_event=None) -> dict:
    """Regenerate the month and build the exact mail for review. Sends nothing."""
    return report_mail_preview.build_preview(
        APP_KEY, partial(build, month=month), user_id, to, cc, subject, intro, allow_unmapped,
        progress_cb=progress_cb, cancel_event=cancel_event,
    )
