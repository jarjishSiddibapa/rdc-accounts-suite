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
from app.services import report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services import xlsx_compact
from app.services.gst_invoice_adder import report_job

APP_KEY = "gst-invoice-adder"
# Aim a little under the hard limit so a part never lands just over it.
_TARGET_PART_BYTES = int(mail.MAX_ATTACHMENT_BYTES * 0.85)
_MAX_SPLIT_ATTEMPTS = 4


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


def _write_parts(df, gst_map: dict, work_dir, stem: str, size_of_whole: int) -> list[str]:
    """Split the rows into parts that each fit in one mail and write them."""
    rows = len(df)
    bytes_per_row = max(size_of_whole / max(rows, 1), 1)
    rows_per_part = max(1000, int(_TARGET_PART_BYTES / bytes_per_row))
    for _ in range(_MAX_SPLIT_ATTEMPTS):
        ranges = [(start, min(start + rows_per_part, rows)) for start in range(0, rows, rows_per_part)]
        paths: list[str] = []
        for index, (start, end) in enumerate(ranges, 1):
            path = str(work_dir / f"{stem}_Part_{index}_of_{len(ranges)}.xlsx")
            run_cpu_phase(report_job.write_workbook, path, df.iloc[start:end].reset_index(drop=True), gst_map)
            xlsx_compact.compact_xlsx_in_place(path)
            paths.append(path)
        if all(_size(path) <= mail.MAX_ATTACHMENT_BYTES for path in paths):
            return paths
        rows_per_part = max(1000, rows_per_part // 2)
    raise RuntimeError("The report could not be split into mails small enough to send.")


def _size(path: str) -> int:
    return Path(path).stat().st_size


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

    whole_size = whole_path.stat().st_size
    payload.detail["attachment"] = "complete workbook"
    if whole_size > mail.MAX_ATTACHMENT_BYTES:
        # The same complete workbook, written more compactly (see xlsx_compact):
        # it opens in Excel exactly as before, at under half the size.
        if progress_cb:
            progress_cb(0.91, "Compacting the workbook to fit an email...")
        xlsx_compact.compact_xlsx_in_place(whole_path)
        whole_size = whole_path.stat().st_size
        payload.detail["attachment"] = "complete workbook (compacted to fit the email limit)"
    if whole_size <= mail.MAX_ATTACHMENT_BYTES:
        payload.attachment_groups = [[str(whole_path)]]
        payload.downloads = [("GST Invoice Number report", str(whole_path))]
    else:
        # Last resort, only if even the compact file does not fit.
        if progress_cb:
            progress_cb(0.92, "Splitting the report into parts that fit in an email...")
        parts = _write_parts(built.df, built.gst_map, work_dir, stem, whole_size)
        whole_path.unlink(missing_ok=True)
        payload.attachment_groups = [[path] for path in parts]
        payload.downloads = [(f"Part {i} of {len(parts)}", path) for i, path in enumerate(parts, 1)]
        payload.detail["attachment"] = f"split into {len(parts)} mails (size limit)"
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
