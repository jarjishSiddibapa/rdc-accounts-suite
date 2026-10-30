"""Automatic and on-demand mail of the RDC Sales Register Report.

One workbook, always one file: the report is fetched from the ERP and written
exactly as the ERP returns it (see app.services.sales_register.report). If it is
over the email size limit it is first written more compactly, then converted to
.xlsb (Excel opens it as it opens .xlsx, with every value unchanged) - it is
never split into several files. See app.services.scheduled_report_mail for
scheduling, recipients and sending.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import SCRATCH_DIR
from app.jobs import JobUserError, run_cpu_phase
from app.regional import now_ist
from app.services import report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services import xlsb_convert, xlsx_compact
from app.services.erp_ageing import oracle_config
from app.services.sales_register import report

APP_KEY = "rdc-sales-register-report"
_ATTACHMENT_LABEL = "RDC Sales Register Report"


def _megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB"


def fit_in_one_file(path: Path, progress_cb=None) -> tuple[Path, str]:
    """The report as the one file to attach, and a note on how it got there.
    Raises JobUserError if even the smallest form is over the email limit: the
    report is never split into several files."""
    limit = mail.MAX_ATTACHMENT_BYTES
    if path.stat().st_size <= limit:
        return path, "complete workbook"

    if progress_cb:
        progress_cb(0.91, "Compacting the workbook to fit one email...")
    xlsx_compact.compact_xlsx_in_place(path)
    if path.stat().st_size <= limit:
        return path, "complete workbook (compacted to fit the email limit)"

    if progress_cb:
        progress_cb(0.93, "Converting the workbook to .xlsb to fit one email...")
    binary = path.with_suffix(".xlsb")
    if xlsb_convert.convert_to_xlsb(path, binary):
        if binary.stat().st_size <= limit:
            return binary, "complete workbook (as .xlsb to fit the email limit)"
        smallest = binary.stat().st_size
    else:
        binary.unlink(missing_ok=True)
        smallest = path.stat().st_size
    raise JobUserError(
        f"The report is {_megabytes(smallest)} even in its smallest form (.xlsb), over the "
        f"{_megabytes(limit)} an email can carry, and it is never split into several files. "
        "Nothing was sent."
    )


def build(snapshot: dict, progress_cb=None, cancel_event=None) -> mail.MailPayload:
    work_dir = SCRATCH_DIR / f"mail-sales-register-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = mail.MailPayload(
        values={"date": "", "period": "", "from_date": "", "to_date": "", "rows": ""}, cleanup=[str(work_dir)],
    )

    run_date = now_ist().date()
    periods = report.get_report_periods(run_date)
    if not periods:
        raise JobUserError("There is no completed day to report on yet.")
    label, start, end = periods[0]

    path = work_dir / f"{report.report_file_stem(start, end)}.xlsx"
    scaled = (lambda frac, msg: progress_cb(frac * 0.9, msg)) if progress_cb else None
    summary = run_cpu_phase(report.build_workbook, oracle_config(), start, end, str(path), progress_cb=scaled)

    final, how = fit_in_one_file(path, progress_cb)

    payload.values = {
        "date": run_date.strftime("%d-%b-%Y"),
        "period": label,
        "from_date": start.strftime("%d-%b-%Y"),
        "to_date": end.strftime("%d-%b-%Y"),
        "rows": f"{summary['rows']:,}",
    }
    payload.detail = {
        "period": label,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rows": summary["rows"],
        "sheets": summary["sheets"],
        "attachment": how,
        "file": final.name,
        "bytes": final.stat().st_size,
    }
    if summary["sheets"] > 1:
        payload.notes.append(
            f"The report has {summary['rows']:,} rows, more than one Excel sheet holds, so it continues on "
            f"{summary['sheets']} sheets of the same file."
        )
    payload.attachment_groups = [[str(final)]]
    payload.downloads = [(_ATTACHMENT_LABEL, str(final))]
    return payload


def run_auto_mail_job(trigger: str = "scheduled", requested_by: str | None = None,
                      progress_cb=None, cancel_event=None) -> dict:
    return mail.run_mail_job(
        APP_KEY, trigger, build, progress_cb=progress_cb, cancel_event=cancel_event, requested_by=requested_by,
    )


def run_preview_job(user_id: int, to: list, cc: list, subject: str, intro: str,
                    allow_unmapped: bool = False, sender: str | None = None,
                    progress_cb=None, cancel_event=None) -> dict:
    """Build the report and the exact mail for review. Sends nothing."""
    return report_mail_preview.build_preview(
        APP_KEY, build, user_id, to, cc, subject, intro, allow_unmapped, sender=sender,
        progress_cb=progress_cb, cancel_event=cancel_event,
    )
