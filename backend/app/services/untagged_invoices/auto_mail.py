"""Automatic mail of the Untagged Invoices Report.

The mail carries the Untagged Summary table, then the Below 1k Summary table,
and the report workbook as the attachment. See
app.services.scheduled_report_mail for scheduling, recipients and sending.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from app.config import SCRATCH_DIR
from app.services import attachment_fit, report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services import xlsb_convert, xlsx_compact  # noqa: F401 - the tests patch them on this module
from app.services.mailer_shared import sheet_to_html
from app.services.untagged_invoices import processor, report_job

APP_KEY = "untagged-invoices-report"

def untagged_splitter(frames, work_dir: Path, as_on: date, look: str = "default"):
    """How the Untagged Invoices workbook is cut when it is too big for an email on its own (see
    app.services.attachment_fit): the four summary/detail sheets first, then the rows of the full Ageing sheet
    in the number of pieces asked for. Every row of every sheet is in exactly one part."""
    df, untagged_summary, untagged_ageing, below_summary, below_ageing = frames
    stem = f"Untagged_Invoices_Report_As_On_{as_on.isoformat()}"

    def write_slice(first_row: int, last_row: int, path: Path) -> None:
        processor.write_report(
            df.iloc[first_row:last_row], untagged_summary, untagged_ageing, below_summary, below_ageing,
            path, as_on, only_full_ageing=True, look=look,
        )

    ageing_slices = attachment_fit.row_splitter(len(df), write_slice, work_dir, f"{stem}_Ageing")

    def split(parts_wanted: int) -> list[Path]:
        first = work_dir / f"{stem}_Part_1_Summaries_and_Detail.xlsx"
        if not first.exists():
            processor.write_report(
                df, untagged_summary, untagged_ageing, below_summary, below_ageing,
                first, as_on, include_full_ageing=False, look=look,
            )
        return [first, *ageing_slices(parts_wanted)]

    return split


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
    payload.intro_note = f"Note: {processor.LAKHS_NOTE}"
    payload.sections = [
        ("Untagged Summary", sheet_to_html(str(summary_path), "Untagged Summary", payload.css, "u")),
        ("Below 1k Summary", sheet_to_html(str(summary_path), "Below 1k Summary", payload.css, "b")),
    ]

    payload.downloads = [("Untagged Invoices Report", str(report_path))]    # the complete file stays downloadable
    # the same rule as every application: compacted, then .xlsb, and only if it is still too big for an email on
    # its own is it split (every row in exactly one part)
    splitter = untagged_splitter(built.frames, work_dir, as_on)
    groups, how, _ = attachment_fit.fit_together([report_path], progress_cb, {report_path: splitter})
    payload.attachment_groups = groups
    payload.detail["attachment"] = how
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
