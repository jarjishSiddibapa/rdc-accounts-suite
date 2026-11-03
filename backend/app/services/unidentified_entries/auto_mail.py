"""Automatic and on-demand mail of the Unidentified Entries.

The Unapplied Receipts Register is fetched from the ERP and its "***** Unidentified"
receipts (each with its Bank Description, the receipt's Comments in the ERP) are
written to one workbook, laid out exactly as the Unidentified Customers sheet of
the Unapplied Receipts report. That report keeps its own sheet; this is the same
list as an application of its own, mailed on its own schedule. See
app.services.scheduled_report_mail for scheduling, recipients and sending.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import SCRATCH_DIR
from app.jobs import run_cpu_phase
from app.regional import now_ist
from app.services import report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services.erp_ageing import oracle_config
from app.services.sales_register.auto_mail import fit_in_one_file
from app.services.unapplied_receipts import processor, report_job
from app.services.unapplied_receipts.erp_register import fetch_register_from_erp

APP_KEY = "unidentified-entries"
# the workbook looks like the Debtors reports' ones (the Unaccounted / MRN / PO look)
LOOK = "accounts"


def build(snapshot: dict, progress_cb=None, cancel_event=None) -> mail.MailPayload:
    work_dir = SCRATCH_DIR / f"mail-unidentified-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = mail.MailPayload(values={"date": "", "rows": ""}, cleanup=[str(work_dir)])

    def report(frac, msg):
        if progress_cb:
            progress_cb(min(float(frac), 1.0) * 0.9, msg)

    as_on = now_ist().date()
    cfg = oracle_config()
    log = report_job.LogQueue(report, start=0.1, end=0.7)

    report(0.02, "Fetching the Unapplied Receipts Register from the ERP...")
    register = fetch_register_from_erp(cfg, None, cancel_event)
    _, unidentified = processor.split_unidentified(register, log, cfg, cancel_event)

    report(0.8, "Writing the workbook...")
    path = work_dir / f"Unidentified_Entries_As_On_{as_on.isoformat()}.xlsx"
    run_cpu_phase(report_job.cpu_write_unidentified, unidentified, str(path), as_on, LOOK)
    final, how = fit_in_one_file(path, progress_cb)

    rows = len(unidentified)
    payload.values = {"date": as_on.strftime("%d-%b-%Y"), "rows": f"{rows:,}"}
    payload.detail = {
        "as_on_date": as_on.isoformat(),
        "rows": rows,
        "attachment": how,
        "file": final.name,
        "bytes": final.stat().st_size,
    }
    payload.attachment_groups = [[str(final)]]
    payload.downloads = [(final.name, str(final))]
    return payload


def run_auto_mail_job(trigger: str = "scheduled", requested_by: str | None = None,
                      progress_cb=None, cancel_event=None) -> dict:
    return mail.run_mail_job(
        APP_KEY, trigger, build, progress_cb=progress_cb, cancel_event=cancel_event, requested_by=requested_by,
    )


def run_preview_job(user_id: int, to: list, cc: list, subject: str, intro: str,
                    allow_unmapped: bool = False, sender: str | None = None,
                    progress_cb=None, cancel_event=None) -> dict:
    """Build the workbook and the exact mail for review. Sends nothing."""
    return report_mail_preview.build_preview(
        APP_KEY, build, user_id, to, cc, subject, intro, allow_unmapped, sender=sender,
        progress_cb=progress_cb, cancel_event=cancel_event,
    )
