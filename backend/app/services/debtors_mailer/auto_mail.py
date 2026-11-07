"""Debtors Reports Auto Mailer: the GST-numbered ageing, the Unapplied Receipts
report and the Untagged Invoices report, built from ONE ageing fetch and mailed
together in a single mail.

The ageing, every customer's region and the unapplied receipts register are
fetched from the ERP once, each on its own connection(s), and the three reports
are built side by side from them. The mail carries the Unapplied summary, the
Untagged summaries and the GST counts, and the three workbooks as attachments.
See app.services.scheduled_report_mail for scheduling, recipients and sending.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path

from app.config import SCRATCH_DIR
from app.database import SessionLocal
from app.jobs import JobCancelled, run_cpu_phase
from app.services import attachment_fit, customer_region, erp_ageing, report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services import xlsb_convert, xlsx_compact
from app.services.debtors_mailer import mapping_store
from app.services.gst_invoice_adder import report_job as gst_job
from app.services.mailer_shared import sheet_to_html
from app.services.untagged_invoices import auto_mail as untagged_auto_mail
from app.services.unapplied_receipts import report_job as unapplied_job
from app.services.unapplied_receipts.erp_register import fetch_register_from_erp
from app.services.untagged_invoices import processor as untagged_processor
from app.services.untagged_invoices import report_job as untagged_job

APP_KEY = "debtors-reports-auto-mailer"
# the three workbooks and the mail tables look like the Unaccounted / MRN / PO reports
LOOK = "accounts"


class _SharedAgeing:
    """The open ageing, fetched from the ERP once however many reports ask for
    it (and whichever asks first). Each caller gets its own copy, so a report
    that edits its frame cannot disturb the others."""

    def __init__(self, oracle_cfg, cancel_event, progress_cb=None):
        self._cfg = oracle_cfg
        self._cancel = cancel_event
        self._progress_cb = progress_cb
        self._lock = threading.Lock()
        self._frame = None
        self.log: list[tuple[str, str]] = []

    def get(self):
        with self._lock:
            if self._frame is None:
                self._frame = erp_ageing.fetch_ageing_from_erp(
                    self._cfg, _ListLog(self.log), progress_cb=self._progress_cb,
                    cancel_event=self._cancel, include_buckets=True,
                )
            return self._frame.copy()


class _ListLog:
    def __init__(self, sink):
        self._sink = sink

    def put(self, item) -> None:
        self._sink.append(item)


def _without_buckets(df):
    """The ageing as the Untagged report takes it: it works out its own buckets."""
    return df.drop(columns=[c for c in erp_ageing._BUCKET_COLUMNS.values() if c in df.columns])


def _cpu_write_untagged(*args):
    run_cpu_phase(untagged_processor.write_report, *args, look=LOOK)


def _merge_warnings(*warning_lists, location_map: dict, incharge_map: dict, regions=()) -> list[dict]:
    """One list of what is unmapped, for both reports. The Unapplied report
    reports a receipt location it cannot map as an unmapped incharge (it keeps
    the raw name as the Location), so a value that is neither a known Location
    (a mapped one, an incharge one or an ERP region) is really a Location Name
    still to be mapped."""
    known_locations = (
        {loc.strip().upper() for loc in location_map.values() if loc}
        | {loc.strip().upper() for loc in incharge_map}
        | {name.strip().upper() for name in (*customer_region.ERP_REGIONS, *regions)}
    )
    merged: dict[str, list[str]] = {}
    for warnings in warning_lists:
        for warning in warnings or []:
            for item in warning["items"]:
                category = warning["category"]
                if category == "Accounts Incharge Not Mapped" and item.strip().upper() not in known_locations:
                    category, item = "Location Not Mapped", item.strip().upper()
                bucket = merged.setdefault(category, [])
                if item not in bucket:
                    bucket.append(item)
    order = ["Location Not Mapped", "Accounts Incharge Not Mapped"]
    categories = sorted(merged, key=lambda c: (order.index(c) if c in order else len(order), c))
    return [{"category": c, "items": sorted(merged[c])} for c in categories]


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


def _fit_attachments(paths: list[Path], progress_cb=None, splitters=None) -> tuple[list[list[str]], str]:
    """The workbooks grouped into as few mails as the size limit allows, and a
    note on what had to be done. Nothing is ever left out: first the same
    workbooks written more compactly, then the largest as .xlsb (Excel opens it
    as it opens .xlsx), then a workbook that is still too big for an email on its own
    is split (``splitters``), and only what cannot share a mail goes in several."""
    groups, how, _ = attachment_fit.fit_together(paths, progress_cb, splitters)
    return groups, how


def build(snapshot: dict, progress_cb=None, cancel_event=None) -> mail.MailPayload:
    work_dir = SCRATCH_DIR / f"mail-debtors-{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = mail.MailPayload(values={"date": "", "total": "", "found": "", "blank": ""}, cleanup=[str(work_dir)])

    as_on = date.today()
    stamp = as_on.isoformat()
    gst_path = work_dir / f"Ageing_with_GST_Invoice_Numbers_As_On_{stamp}.xlsx"
    unapplied_path = work_dir / f"Unapplied_Receipts_Report_As_On_{stamp}.xlsx"
    untagged_path = work_dir / f"Untagged_Invoices_Report_As_On_{stamp}.xlsx"

    # progress: each report counts for a third of the first 90 %
    fractions = {"gst": 0.0, "unapplied": 0.0, "untagged": 0.0}
    progress_lock = threading.Lock()

    def reporter(name: str):
        def report(frac, msg):
            if progress_cb is None:
                return
            with progress_lock:
                fractions[name] = max(fractions[name], min(float(frac), 1.0))
                overall = 0.02 + 0.88 * sum(fractions.values()) / len(fractions)
            progress_cb(overall, f"{name.capitalize()}: {msg}")
        return report

    if progress_cb:
        progress_cb(0.01, "Loading the mappings...")
    db = SessionLocal()
    try:
        location_map, incharge_map = mapping_store.load_all(db)
    finally:
        db.close()

    cfg = erp_ageing.oracle_config()
    stop = threading.Event()
    cancel = erp_ageing.EitherEvent(cancel_event, stop)
    allow_unmapped = bool(snapshot["send_when_unmapped"])

    # Fetched once, from the ERP, for all three reports (each on its own connection(s)).
    shared = _SharedAgeing(cfg, cancel)
    regions = customer_region.RegionFetch(cfg, cancel)

    def build_gst():
        return gst_job.build_report(
            str(gst_path), gst_path.name, progress_cb=reporter("gst"), cancel_event=cancel, ageing=shared.get,
            look=LOOK,
        )

    def build_untagged():
        result = untagged_job.build_report(
            str(untagged_path), allow_unmapped=allow_unmapped, progress_cb=reporter("untagged"),
            cancel_event=cancel, maps=(location_map, incharge_map),
            ageing=lambda: _without_buckets(shared.get()), regions=regions.result,
            write=_cpu_write_untagged,
        )
        # A workbook that is over the email limit by itself is always compacted by _fit_attachments
        # (nothing can bring the total under the limit without it), so do it now, while the other reports
        # are still waiting on the ERP, instead of afterwards. The same result, sooner.
        if untagged_path.exists() and untagged_path.stat().st_size > mail.MAX_ATTACHMENT_BYTES:
            xlsx_compact.compact_xlsx_in_place(untagged_path)
        return result

    def build_unapplied():
        register = fetch_register_from_erp(cfg, None, cancel)
        return unapplied_job.build_report(
            str(unapplied_path), cfg, (incharge_map, location_map), register, shared.get(),
            regions.result(), allow_unmapped=allow_unmapped, progress_cb=reporter("unapplied"),
            cancel_event=cancel, look=LOOK,
        )

    jobs = {"gst": build_gst, "untagged": build_untagged, "unapplied": build_unapplied}
    first_error: list[BaseException] = []

    def guarded(fn):
        def run():
            try:
                return fn()
            except BaseException as exc:
                if not first_error:
                    first_error.append(exc)
                stop.set()          # the others are not needed any more
                raise
        return run

    with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="debtors-mail") as pool:
        futures = {name: pool.submit(guarded(fn)) for name, fn in jobs.items()}
        wait(list(futures.values()), return_when=FIRST_EXCEPTION)
        if first_error:
            stop.set()
    if first_error:
        raise first_error[0]
    if cancel_event is not None and cancel_event.is_set():
        raise JobCancelled("Cancelled")

    gst = futures["gst"].result()
    untagged = futures["untagged"].result()
    unapplied = futures["unapplied"].result()
    gst_result, untagged_result = gst.result, untagged.result

    payload.values = {
        "date": as_on.strftime("%d-%b-%Y"),
        "total": f"{gst_result['total']:,}",
        "found": f"{gst_result['found']:,}",
        "blank": f"{gst_result['blank']:,}",
    }
    warnings = _merge_warnings(
        untagged_result.get("validation_warnings"), unapplied.get("validation_warnings"),
        location_map=location_map, incharge_map=incharge_map, regions=set(regions.result().values()),
    )
    payload.detail = {
        "as_on_date": as_on.isoformat(),
        "ageing_rows": gst_result["total"],
        "gst_found": gst_result["found"],
        "gst_blank": gst_result["blank"],
        "untagged_summary_locations": untagged_result.get("untagged_summary_row_count"),
        "untagged_ageing_rows": untagged_result.get("untagged_ageing_row_count"),
        "below_1k_ageing_rows": untagged_result.get("below_1k_ageing_row_count"),
        "unapplied_rows": unapplied.get("main_row_count"),
        "unapplied_advance_rows": unapplied.get("advance_row_count"),
        "unmapped": warnings,
    }
    if (untagged_result["needs_mapping_fix"] or unapplied["needs_mapping_fix"]) and not allow_unmapped:
        payload.hold_reason = _hold_reason(warnings)
        return payload

    # the summaries shown in the mail, formatted exactly as in the reports
    summary_path = work_dir / "summaries.xlsx"
    untagged_processor.write_summary_workbook(
        untagged.untagged_summary, untagged.below_1k_summary, summary_path, look=LOOK)
    payload.intro_note = f"Note: {untagged_processor.LAKHS_NOTE}"
    # The ageing with GST numbers is only an attachment (the intro lists it). The reports carry
    # no title bar or lakhs label above their tables; the heading above each table names it and
    # the note above the sections says the amounts are in lakhs.
    payload.sections = [
        ("2. Unapplied Receipts (Summary):",
         sheet_to_html(str(unapplied_path), "Summary", payload.css, "r")),
        ("3. Untagged Invoices (Summary):", sheet_to_html(str(summary_path), "Untagged Summary", payload.css, "u")),
        ("4. Below 1k Invoices (Summary):", sheet_to_html(str(summary_path), "Below 1k Summary", payload.css, "b")),
    ]

    # A workbook that is still too big for an email on its own (even as .xlsb) is split as the last resort: the
    # ageing by rows, the Untagged report as its own sheets plus slices of its full ageing. Every row lands in
    # exactly one part. The Unapplied Receipts workbook has no splitter: if it is ever that big, the job stops.
    def write_gst_slice(first: int, last: int, part_path: Path) -> None:
        part = gst.df.iloc[first:last].reset_index(drop=True)
        run_cpu_phase(gst_job.write_workbook, str(part_path), part, gst.gst_map, look=LOOK)

    splitters = {
        gst_path: attachment_fit.row_splitter(len(gst.df), write_gst_slice, work_dir, gst_path.stem),
        untagged_path: untagged_auto_mail.untagged_splitter(untagged.frames, work_dir, as_on, look=LOOK),
    }
    groups, how = _fit_attachments([gst_path, unapplied_path, untagged_path], progress_cb, splitters)
    payload.attachment_groups = groups
    payload.detail["attachment"] = how
    payload.downloads = [(Path(p).name, p) for group in groups for p in group]
    return payload


def run_auto_mail_job(trigger: str = "scheduled", requested_by: str | None = None,
                      progress_cb=None, cancel_event=None) -> dict:
    return mail.run_mail_job(
        APP_KEY, trigger, build, progress_cb=progress_cb, cancel_event=cancel_event, requested_by=requested_by,
    )


def run_preview_job(user_id: int, to: list, cc: list, subject: str, intro: str,
                    allow_unmapped: bool = False, sender: str | None = None,
                    progress_cb=None, cancel_event=None) -> dict:
    """Build the reports and the exact mail for review. Sends nothing."""
    return report_mail_preview.build_preview(
        APP_KEY, build, user_id, to, cc, subject, intro, allow_unmapped, sender=sender,
        progress_cb=progress_cb, cancel_event=cancel_event,
    )
