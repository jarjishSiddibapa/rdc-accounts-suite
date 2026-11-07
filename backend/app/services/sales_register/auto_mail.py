"""Automatic and on-demand mail of the RDC Sales Register Report.

One workbook per calendar month (see report.get_report_periods): each is fetched
from the ERP and written exactly as the ERP returns it (see
app.services.sales_register.report). If a month's file is over the email size
limit it is first written more compactly, then converted to .xlsb (Excel opens
it as it opens .xlsx, with every value unchanged) - a month is never split into
several files. The month files go in one mail, or in one mail each if together
they are over the limit: first the files are made smaller together (see
app.services.attachment_fit), and only if they still cannot share one mail are they
spread over as few mails as possible. See app.services.scheduled_report_mail for
scheduling, recipients and sending.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import SCRATCH_DIR
from app.jobs import JobUserError, run_cpu_phase
from app.regional import now_ist
from app.services import attachment_fit, report_mail_preview
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
        values={"date": "", "report": "", "period": "", "from_date": "", "to_date": "", "rows": ""}, cleanup=[str(work_dir)],
    )

    run_date = now_ist().date()
    periods = report.get_report_periods(run_date)
    if not periods:
        raise JobUserError("There is no completed day to report on yet.")

    # one file per month: each is fetched, written and fitted to the email limit on its own
    files = []                       # (label, start, end, summary, final path, how)
    for index, (label, start, end) in enumerate(periods):
        low, span = index / len(periods), 1 / len(periods)
        scaled = (lambda frac, msg, low=low, span=span: progress_cb((low + frac * span) * 0.9, msg)) if progress_cb else None
        path = work_dir / f"{report.report_file_stem(start, end)}.xlsx"
        summary = run_cpu_phase(report.build_workbook, oracle_config(), start, end, str(path), progress_cb=scaled)
        final, how = fit_in_one_file(path, progress_cb)
        files.append((label, start, end, summary, final, how))

    # One email carries a limited total, not a limited size per file. When the month files together are over it,
    # they are made smaller together (compacted, then .xlsb) so they still go in ONE mail; only if that is still
    # not enough are they spread over as few mails as possible.
    groups, fitted_how, finals = attachment_fit.fit_together([f[4] for f in files], progress_cb)
    files = [(label, start, end, summary, final, how if fitted_how == "complete workbooks" else fitted_how)
             for (label, start, end, summary, _, how), final in zip(files, finals)]

    first_start, last_end = files[0][1], files[-1][2]
    total_rows = sum(f[3]["rows"] for f in files)
    payload.values = {
        "date": run_date.strftime("%d-%b-%Y"),
        "report": _ATTACHMENT_LABEL + (" files" if len(files) > 1 else ""),
        "period": " + ".join(f[0] for f in files),
        "from_date": first_start.strftime("%d-%b-%Y"),
        "to_date": last_end.strftime("%d-%b-%Y"),
        "rows": f"{total_rows:,}",
    }
    payload.detail = {
        "period": payload.values["period"],
        "from": first_start.isoformat(),
        "to": last_end.isoformat(),
        "rows": total_rows,
        "attachment": files[0][5] if len(files) == 1 else (
            f"{len(files)} files, one per month"
            + ("" if fitted_how == "complete workbooks" else f" ({fitted_how})")
        ),
        "files": [
            {"period": label, "from": start.isoformat(), "to": end.isoformat(), "rows": summary["rows"],
             "sheets": summary["sheets"], "attachment": how, "file": final.name, "bytes": final.stat().st_size}
            for label, start, end, summary, final, how in files
        ],
    }
    for label, _, _, summary, _, _ in files:
        if summary["sheets"] > 1:
            payload.notes.append(
                f"The {label} file has {summary['rows']:,} rows, more than one Excel sheet holds, so it "
                f"continues on {summary['sheets']} sheets of the same file."
            )

    payload.attachment_groups = groups
    payload.downloads = [
        (f"{_ATTACHMENT_LABEL} - {f[0]}" if len(files) > 1 else _ATTACHMENT_LABEL, str(f[4])) for f in files
    ]
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
