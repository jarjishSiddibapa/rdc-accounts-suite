"""Automatic and on-demand mail of the RDC Sales Register Report.

One workbook per calendar month (see report.get_report_periods): each is fetched
from the ERP and written exactly as the ERP returns it (see
app.services.sales_register.report). All the files go in ONE mail. Because one
email carries a limited total (not a limit per file), files that are too big together
are made smaller first, with every value unchanged: written more compactly, then
converted to .xlsb (Excel opens it as it opens .xlsx). Only if a single month is
still too big for an email on its own is it split, as the last resort, into the
fewest date ranges that fit (every day lands in exactly one part, nothing is left
out); see app.services.attachment_fit. See app.services.scheduled_report_mail for
scheduling, recipients and sending.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path

from app.config import SCRATCH_DIR
from app.jobs import JobUserError, run_cpu_phase
from app.regional import now_ist
from app.services import attachment_fit, report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services.erp_ageing import oracle_config
from app.services.sales_register import report

APP_KEY = "rdc-sales-register-report"
_ATTACHMENT_LABEL = "RDC Sales Register Report"


def _day_ranges(start: date, end: date, count: int) -> list[tuple[date, date]]:
    """``count`` consecutive date ranges that together cover start..end exactly: no gap, no overlap."""
    days = (end - start).days + 1
    ranges, first = [], start
    for index in range(count):
        length = days // count + (1 if index < days % count else 0)
        last = first + timedelta(days=length - 1)
        ranges.append((first, last))
        first = last + timedelta(days=1)
    return ranges


def _month_splitter(start: date, end: date, work_dir: Path, state: dict):
    """How one month's workbook is cut when it is too big for an email on its own: into consecutive date ranges,
    each fetched from the ERP and written as a complete workbook of its own."""
    def split(parts_wanted: int) -> list[Path]:
        days = (end - start).days + 1
        if days < 2 or state.get("at_finest"):
            raise attachment_fit.CannotSplit("it is a single day, which cannot be split any further.")
        count = min(parts_wanted, days)
        state["at_finest"] = count == days
        parts, summaries = [], []
        for first, last in _day_ranges(start, end, count):
            path = work_dir / f"{report.report_file_stem(first, last)}.xlsx"
            summaries.append(run_cpu_phase(report.build_workbook, oracle_config(), first, last, str(path)))
            parts.append(path)
        state["parts"], state["summaries"] = parts, summaries
        return parts
    return split


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

    # one file per month: each is fetched and written on its own
    months = []                       # label, start, end, summary, path, split state
    for index, (label, start, end) in enumerate(periods):
        low, span = index / len(periods), 1 / len(periods)
        scaled = (lambda frac, msg, low=low, span=span: progress_cb((low + frac * span) * 0.9, msg)) if progress_cb else None
        path = work_dir / f"{report.report_file_stem(start, end)}.xlsx"
        summary = run_cpu_phase(report.build_workbook, oracle_config(), start, end, str(path), progress_cb=scaled)
        months.append((label, start, end, summary, path, {}))

    # One email carries a limited TOTAL, so the files are made smaller together (compacted, then .xlsb) and go in
    # ONE mail. Only a month that is still too big on its own is split; only what cannot share a mail is spread.
    splitters = {path: _month_splitter(start, end, work_dir, state) for _, start, end, _, path, state in months}
    groups, fitted_how, finals = attachment_fit.fit_together([m[4] for m in months], progress_cb, splitters)

    def files_of(month) -> list[Path]:
        """The final files that belong to a month: its own file (maybe converted) or its parts."""
        stems = {p.stem for p in month[5].get("parts", [])} or {month[4].stem}
        return [final for final in finals if final.stem in stems]

    def rows_of(month) -> int:
        return sum(s["rows"] for s in month[5]["summaries"]) if month[5].get("parts") and files_of(month) else month[3]["rows"]

    first_start, last_end = months[0][1], months[-1][2]
    total_rows = sum(rows_of(m) for m in months)
    payload.values = {
        "date": run_date.strftime("%d-%b-%Y"),
        "report": _ATTACHMENT_LABEL + (" files" if len(finals) > 1 else ""),
        "period": " + ".join(m[0] for m in months),
        "from_date": first_start.strftime("%d-%b-%Y"),
        "to_date": last_end.strftime("%d-%b-%Y"),
        "rows": f"{total_rows:,}",
    }
    how = fitted_how
    payload.detail = {
        "period": payload.values["period"],
        "from": first_start.isoformat(),
        "to": last_end.isoformat(),
        "rows": total_rows,
        "attachment": how if len(months) == 1 else (
            f"{len(months)} files, one per month"
            + ("" if fitted_how in attachment_fit.UNCHANGED else f" ({fitted_how})")
        ),
        "files": [
            {"period": m[0], "from": m[1].isoformat(), "to": m[2].isoformat(), "rows": rows_of(m),
             "sheets": m[3]["sheets"], "attachment": fitted_how,
             "file": ", ".join(f.name for f in files_of(m)), "bytes": sum(f.stat().st_size for f in files_of(m))}
            for m in months
        ],
    }
    for label, _, _, summary, _, state in months:
        if summary["sheets"] > 1 and not state.get("parts"):
            payload.notes.append(
                f"The {label} file has {summary['rows']:,} rows, more than one Excel sheet holds, so it "
                f"continues on {summary['sheets']} sheets of the same file."
            )

    payload.attachment_groups = groups
    downloads = []
    for month in months:
        month_files = files_of(month)
        for number, final in enumerate(month_files, 1):
            name = _ATTACHMENT_LABEL if len(months) == 1 else f"{_ATTACHMENT_LABEL} - {month[0]}"
            if len(month_files) > 1:
                name += f" (part {number} of {len(month_files)})"
            downloads.append((name, str(final)))
    payload.downloads = downloads
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
