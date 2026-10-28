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
from app.services import customer_region, erp_ageing, report_mail_preview
from app.services import scheduled_report_mail as mail
from app.services import xlsb_convert, xlsx_compact
from app.services.debtors_mailer import mapping_store
from app.services.gst_invoice_adder import report_job as gst_job
from app.services.mailer_shared import sheet_to_html
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


def _fit_attachments(paths: list[Path], progress_cb=None) -> tuple[list[list[str]], str]:
    """The workbooks grouped into as few mails as the size limit allows, and a
    note on what had to be done. Nothing is ever left out: first the same
    workbooks written more compactly, then the largest as .xlsb (Excel opens it
    as it opens .xlsx), and only if that is still too much, several mails."""
    limit = mail.MAX_ATTACHMENT_BYTES

    def total(items):
        return sum(Path(p).stat().st_size for p in items)

    if total(paths) <= limit:
        return [[str(p) for p in paths]], "complete workbooks"

    if progress_cb:
        progress_cb(0.91, "Compacting the workbooks to fit one email...")
    for path in sorted(paths, key=lambda p: p.stat().st_size, reverse=True):
        if total(paths) <= limit:
            break
        if path.suffix == ".xlsx" and path.stat().st_size > 1_000_000:
            xlsx_compact.compact_xlsx_in_place(path)
    if total(paths) <= limit:
        return [[str(p) for p in paths]], "complete workbooks (compacted to fit the email limit)"

    current = list(paths)
    for path in sorted(paths, key=lambda p: p.stat().st_size, reverse=True):
        if total(current) <= limit:
            break
        if progress_cb:
            progress_cb(0.93, f"Converting {path.name} to .xlsb to fit one email...")
        binary = path.with_suffix(".xlsb")
        if xlsb_convert.convert_to_xlsb(path, binary) and binary.stat().st_size < path.stat().st_size:
            current[current.index(path)] = binary
        else:
            binary.unlink(missing_ok=True)
    if total(current) <= limit:
        return [[str(p) for p in current]], "complete workbooks (the largest as .xlsb to fit the email limit)"

    groups: list[list[Path]] = []
    for path in sorted(current, key=lambda p: p.stat().st_size, reverse=True):
        for group in groups:
            if total(group) + path.stat().st_size <= limit:
                group.append(path)
                break
        else:
            groups.append([path])
    order = {p: i for i, p in enumerate(current)}
    return (
        [[str(p) for p in sorted(g, key=order.get)] for g in groups],
        f"complete workbooks in {len(groups)} mails (size limit)",
    )


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
        return untagged_job.build_report(
            str(untagged_path), allow_unmapped=allow_unmapped, progress_cb=reporter("untagged"),
            cancel_event=cancel, maps=(location_map, incharge_map),
            ageing=lambda: _without_buckets(shared.get()), regions=regions.result,
            write=_cpu_write_untagged,
        )

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
    # The ageing with GST numbers is only an attachment (the intro lists it). The Unapplied
    # sheet's first two rows are its title bar ("... | Location × Ageing | ...") and the lakhs
    # label; both are left out of the mail because the heading above the table names it and
    # the note above the sections already says the amounts are in lakhs.
    payload.sections = [
        ("2. Unapplied Receipts (Summary):",
         sheet_to_html(str(unapplied_path), "Summary", payload.css, "r", skip_rows=(1, 2))),
        ("3. Untagged Invoices (Summary):", sheet_to_html(str(summary_path), "Untagged Summary", payload.css, "u")),
        ("4. Below 1k Invoices (Summary):", sheet_to_html(str(summary_path), "Below 1k Summary", payload.css, "b")),
    ]

    groups, how = _fit_attachments([gst_path, unapplied_path, untagged_path], progress_cb)
    payload.attachment_groups = groups
    payload.detail["attachment"] = how
    payload.downloads = [(Path(p).name, p) for group in groups for p in group]
    if len(groups) > 1:
        payload.notes.append(
            "The three reports are too large for a single email, so they are sent in "
            f"{len(groups)} mails. Nothing has been left out."
        )
    return payload


def run_auto_mail_job(trigger: str = "scheduled", requested_by: str | None = None,
                      progress_cb=None, cancel_event=None) -> dict:
    return mail.run_mail_job(
        APP_KEY, trigger, build, progress_cb=progress_cb, cancel_event=cancel_event, requested_by=requested_by,
    )


def run_preview_job(user_id: int, to: list, cc: list, subject: str, intro: str,
                    allow_unmapped: bool = False, progress_cb=None, cancel_event=None) -> dict:
    """Build the reports and the exact mail for review. Sends nothing."""
    return report_mail_preview.build_preview(
        APP_KEY, build, user_id, to, cc, subject, intro, allow_unmapped,
        progress_cb=progress_cb, cancel_event=cancel_event,
    )
