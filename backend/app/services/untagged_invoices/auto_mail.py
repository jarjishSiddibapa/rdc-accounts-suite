"""Automatic mail of the Untagged Invoices Report.

The mail carries the Untagged Summary table, then the Below 1k Summary table,
and the report workbook as the attachment. See
app.services.scheduled_report_mail for scheduling, recipients and sending.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.config import SCRATCH_DIR
from app.services import report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services import xlsx_compact
from app.services.mailer_shared import sheet_to_html
from app.services.untagged_invoices import processor, report_job

APP_KEY = "untagged-invoices-report"

_NO_FULL_AGEING_NOTE = (
    "Even compacted, the complete workbook is too large to attach to an email, so the full \"Ageing\" "
    "sheet (every open item) is left out of this attachment. Download the complete report from the "
    "Untagged Invoices Report page in the application."
)
_NO_ATTACHMENT_NOTE = (
    "The report is too large to attach to an email. Download it from the Untagged Invoices Report "
    "page in the application."
)


def _hold_reason(warnings: list[dict]) -> str:
    parts = []
    for warning in warnings:
        items = warning["items"]
        shown = ", ".join(items[:5]) + (f" and {len(items) - 5} more" if len(items) > 5 else "")
        parts.append(f"{warning['category']} ({len(items)}): {shown}")
    return (
        "The mail was not sent because some values have no mapping - " + "; ".join(parts)
        + ". Add them in the mapping tables, or allow the mail to go out with them shown as \"-\"."
    )


def build(snapshot: dict, progress_cb=None, cancel_event=None) -> mail.MailPayload:
    work_dir = SCRATCH_DIR / f"mail-untagged-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = mail.MailPayload(values={"date": ""}, cleanup=[str(work_dir)])

    scaled = (lambda frac, msg: progress_cb(frac * 0.9, msg)) if progress_cb else None
    # the file name is what the recipients see on the attachment
    as_on = date.today()
    report_path = work_dir / f"Untagged_Invoices_Report_As_On_{as_on.isoformat()}.xlsx"
    built = report_job.build_report(
        str(report_path), allow_unmapped=snapshot["send_when_unmapped"],
        progress_cb=scaled, cancel_event=cancel_event,
    )
    result = built.result
    as_on = built.as_on_date or as_on
    payload.values = {"date": as_on.strftime("%d-%b-%Y")}
    payload.detail = {
        "as_on_date": as_on.isoformat(),
        "rows": result["total_rows"],
        "unmapped": result.get("validation_warnings") or [],
    }
    if result["needs_mapping_fix"]:
        payload.hold_reason = _hold_reason(result["validation_warnings"])
        return payload

    # the two summary tables, rendered from the same formatting as the report
    summary_path = work_dir / "summaries.xlsx"
    processor.write_summary_workbook(built.untagged_summary, built.below_1k_summary, summary_path)
    # compact markup: with every style inline the two tables run to ~190 KB and
    # Gmail clips a message over ~102 KB
    payload.sections = [
        ("Untagged Summary", sheet_to_html(str(summary_path), "Untagged Summary", payload.css, "u")),
        ("Below 1k Summary", sheet_to_html(str(summary_path), "Below 1k Summary", payload.css, "b")),
    ]

    payload.downloads = [("Untagged Invoices Report", str(report_path))]
    attachment = report_path
    if report_path.stat().st_size > mail.MAX_ATTACHMENT_BYTES:
        # The same complete workbook, written more compactly (see xlsx_compact):
        # it opens in Excel exactly as before, at under half the size.
        xlsx_compact.compact_xlsx_in_place(report_path)
        payload.detail["attachment"] = "complete workbook (compacted to fit the email limit)"
    else:
        payload.detail["attachment"] = "complete workbook"
    if report_path.stat().st_size > mail.MAX_ATTACHMENT_BYTES:
        # Last resort, only if even the compact file does not fit.
        df, untagged_summary, untagged_ageing, below_summary, below_ageing = built.frames
        attachment = work_dir / f"Untagged_Invoices_Report_As_On_{as_on.isoformat()}_Without_Full_Ageing.xlsx"
        processor.write_report(
            df, untagged_summary, untagged_ageing, below_summary, below_ageing,
            attachment, as_on, include_full_ageing=False,
        )
        payload.detail["attachment"] = "without the full Ageing sheet (size limit)"
        if attachment.stat().st_size > mail.MAX_ATTACHMENT_BYTES:
            payload.notes.append(_NO_ATTACHMENT_NOTE)
            payload.detail["attachment"] = "none (size limit)"
            attachment = None
        else:
            payload.notes.append(_NO_FULL_AGEING_NOTE)

    payload.attachment_groups = [[str(attachment)]] if attachment else [[]]
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
