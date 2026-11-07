"""Automatic mail of the GST Invoice Number report.

The workbook (the receivable ageing with the GST Invoice Number column) is the
attachment; the body shows how many rows were found. The complete workbook is
well over the email size limit at current volumes, so it is split by rows into
as many parts as needed, each sent as its own mail. See
app.services.scheduled_report_mail for scheduling, recipients and sending.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from app.config import SCRATCH_DIR
from app.jobs import run_cpu_phase
from app.services import attachment_fit, report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services import xlsb_convert, xlsx_compact  # noqa: F401 - the tests patch them on this module
from app.services.gst_invoice_adder import report_job

APP_KEY = "gst-invoice-adder"


def _stats_table(total: int, found: int, blank: int) -> str:
    cell = "padding:6px 14px;border:1px solid #000000;text-align:right"
    head = "padding:6px 14px;border:1px solid #000000;background:#1F3864;color:#ffffff;text-align:center"
    return (
        "<table style='border-collapse:collapse;font-size:11pt'>"
        f"<tr><th style='{head}'>Total rows</th><th style='{head}'>GST number found</th>"
        f"<th style='{head}'>No GST number</th></tr>"
        f"<tr><td style='{cell}'>{total:,}</td><td style='{cell}'>{found:,}</td>"
        f"<td style='{cell}'>{blank:,}</td></tr></table>"
    )


def build(snapshot: dict, progress_cb=None, cancel_event=None) -> mail.MailPayload:
    work_dir = SCRATCH_DIR / f"mail-gst-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = mail.MailPayload(values={"date": "", "total": "", "found": "", "blank": ""}, cleanup=[str(work_dir)])

    scaled = (lambda frac, msg: progress_cb(frac * 0.9, msg)) if progress_cb else None
    as_on = date.today()
    stem = f"GST_Invoice_Numbers_As_On_{as_on.isoformat()}"
    whole_path = work_dir / f"{stem}.xlsx"
    built = report_job.build_report(str(whole_path), f"{stem}.xlsx", progress_cb=scaled, cancel_event=cancel_event)
    result = built.result

    payload.values = {
        "date": as_on.strftime("%d-%b-%Y"),
        "total": f"{result['total']:,}",
        "found": f"{result['found']:,}",
        "blank": f"{result['blank']:,}",
    }
    payload.detail = {
        "as_on_date": as_on.isoformat(),
        "total": result["total"],
        "found": result["found"],
        "blank": result["blank"],
    }
    payload.sections = [("Summary", _stats_table(result["total"], result["found"], result["blank"]))]

    def write_slice(first: int, last: int, part_path: Path) -> None:
        part = built.df.iloc[first:last].reset_index(drop=True)
        run_cpu_phase(report_job.write_workbook, str(part_path), part, built.gst_map)

    # the same rule as every application: compacted, then .xlsb, and only if it is still too big for an email on
    # its own is it split by rows (every row in exactly one part)
    splitter = attachment_fit.row_splitter(len(built.df), write_slice, work_dir, stem)
    groups, how, finals = attachment_fit.fit_together([whole_path], progress_cb, {whole_path: splitter})
    payload.attachment_groups = groups
    payload.detail["attachment"] = how
    if len(finals) == 1:
        # one file (as built, compacted or .xlsb): the complete workbook stays downloadable
        payload.downloads = [("GST Invoice Number report", str(whole_path))]
    else:
        whole_path.unlink(missing_ok=True)
        payload.downloads = [(f"Part {i} of {len(finals)}", str(path)) for i, path in enumerate(finals, 1)]
    return payload


def run_auto_mail_job(trigger: str = "scheduled", requested_by: str | None = None,
                      progress_cb=None, cancel_event=None) -> dict:
    return mail.run_mail_job(
        APP_KEY, trigger, build, progress_cb=progress_cb, cancel_event=cancel_event, requested_by=requested_by,
    )


def run_preview_job(user_id: int, to: list, cc: list, subject: str, intro: str,
                    allow_unmapped: bool = False, progress_cb=None, cancel_event=None) -> dict:
    """Build the report and the exact mail for review. Sends nothing."""
    return report_mail_preview.build_preview(
        APP_KEY, build, user_id, to, cc, subject, intro, allow_unmapped,
        progress_cb=progress_cb, cancel_event=cancel_event,
    )
