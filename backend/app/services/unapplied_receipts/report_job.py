"""Build the Unapplied Receipts report from ERP data.

The web job (app.routers.unapplied_receipts) and the Debtors Reports Auto Mailer
both use the log adapters and the two CPU-pool steps below; ``build_report``
is the mailer's whole pipeline, working from data it has already fetched (the
register, the ageing and the customers' regions) so each is fetched once.
"""

from __future__ import annotations

from datetime import date

from app.jobs import run_cpu_phase
from app.services.unapplied_receipts import processor


class LogQueue:
    """Adapts processor.py's ``log_q.put((level, msg))`` calls to a background
    job's ``progress_cb(frac, phase)``, keeping every message so it can be
    returned to the caller for display."""

    def __init__(self, progress_cb=None, start: float = 0.05, end: float = 0.85):
        self._progress_cb = progress_cb
        self._start = start
        self._end = end
        self._count = 0
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        level, msg = item
        self.messages.append((level, msg))
        if self._progress_cb:
            self._count += 1
            # Nudge the fraction along without knowing the real total steps.
            frac = min(self._end, self._start + self._count * 0.03)
            try:
                self._progress_cb(frac, msg)
            except Exception:
                pass


class CollectingLogQueue:
    """Plain picklable log_q: just accumulates (level, message) tuples. Used
    inside the CPU-pool steps, which run in a subprocess; their messages come
    back in the return value and are merged into the caller's log."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def cpu_classify_and_validate(df, ageing_path, incharge_map, supplier_site_map, ageing_frame=None):
    """Classify + validate only - pure pandas CPU work with no I/O, so this
    runs on the CPU process pool. Split out from the write step below so the
    caller can skip the (much more expensive) workbook write entirely when
    mappings are missing."""
    log_q = CollectingLogQueue()
    df_main, df_advance, _salesperson_map = processor.classify_advance_customers(
        df, ageing_path, log_q, ageing_frame=ageing_frame,
    )
    validation_errors = processor._validate_before_save(df_main, incharge_map, supplier_site_map)
    validation_warnings = [
        {"category": category, "items": items} for category, items in validation_errors
    ]
    for category, items in validation_errors:
        log_q.put(("warn", f"{category}: {len(items)} unmapped value(s)"))
    return df_main, df_advance, validation_warnings, log_q.messages


def cpu_write_report(df_main, df_advance, output_path, as_on_date, df_unidentified,
                     incharge_map, progress_cb=None, look="default"):
    log_q = CollectingLogQueue()
    processor.write_formatted_excel(
        df_main, df_advance, output_path, as_on_date,
        df_unidentified=df_unidentified, incharge_map=incharge_map, log_q=log_q,
        progress_cb=progress_cb, look=look,
    )
    return log_q.messages


def slim_ageing(ageing_frame):
    """Only the three ageing columns the advance-customer check reads; a
    slimmer frame is quicker to hand to the CPU process."""
    from app.services.erp_ageing import TYPE_COL
    return ageing_frame[["Customer Account", TYPE_COL, "Sales Person Name"]]


def build_report(output_path: str, oracle_cfg, maps, register_frame, ageing_frame, region_by_account,
                 allow_unmapped: bool = False, progress_cb=None, cancel_event=None,
                 as_on_date: date | None = None, look: str = "default") -> dict:
    """The report from data already fetched. ``maps`` = (incharge_map,
    location_map); the result has the same shape as the web job's."""
    incharge_map, supplier_site_map = maps
    as_on_date = as_on_date or date.today()
    log_q = LogQueue(progress_cb)

    df, total_input_rows, unidentified_removed_count, df_unidentified, erp_ok = processor.process_report(
        "", log_q, as_on_date,
        oracle_cfg=oracle_cfg, supplier_site_map=supplier_site_map, cancel_event=cancel_event,
        region_lookup=lambda: region_by_account, register_frame=register_frame,
    )
    df_main, df_advance, validation_warnings, cpu_messages = run_cpu_phase(
        cpu_classify_and_validate, df, None, incharge_map, supplier_site_map, slim_ageing(ageing_frame),
    )
    log_q.messages.extend(cpu_messages)

    base = {
        "total_input_rows": total_input_rows,
        "unidentified_removed_count": unidentified_removed_count,
        "main_row_count": len(df_main),
        "advance_row_count": len(df_advance),
        "validation_warnings": validation_warnings,
        "oracle_ok": erp_ok,
    }
    if validation_warnings and not allow_unmapped:
        log_q.put(("warn", "Fix the mappings above and regenerate - report was not written yet."))
        return {"needs_mapping_fix": True, **base, "log": log_q.messages}

    cpu_messages = run_cpu_phase(
        cpu_write_report, df_main, df_advance, output_path, as_on_date, df_unidentified, incharge_map,
        look=look,
    )
    log_q.messages.extend(cpu_messages)
    return {
        "needs_mapping_fix": False,
        "allowed_unmapped": bool(validation_warnings),
        "output_path": str(output_path),
        "as_on_date": as_on_date.isoformat(),
        **base,
        "log": log_q.messages,
    }
