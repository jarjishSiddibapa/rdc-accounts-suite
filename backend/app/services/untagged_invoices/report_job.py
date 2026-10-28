"""Build the Untagged Invoices Report from the live ERP ageing.

Shared by the on-demand report job (the web page) and the automatic mail job,
so both produce exactly the same workbook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.database import SessionLocal
from app.services import customer_region
from app.services.erp_ageing import oracle_config
from app.services.untagged_invoices import mapping_store, processor


class LogQueue:
    """Collects the processor's log lines and turns them into job progress."""

    def __init__(self, progress_cb=None, start: float = 0.05, end: float = 0.5):
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
            frac = min(self._end, self._start + self._count * 0.03)
            try:
                self._progress_cb(frac, msg)
            except Exception:
                pass


@dataclass
class BuiltReport:
    """The job result, plus the tables the automatic mail needs. The tables
    are None while the report is held back for missing mappings."""

    result: dict
    as_on_date: date | None = None
    frames: tuple[pd.DataFrame, ...] | None = None   # ageing, untagged summary, untagged ageing, below-1k summary, below-1k ageing

    @property
    def untagged_summary(self) -> pd.DataFrame | None:
        return self.frames[1] if self.frames else None

    @property
    def below_1k_summary(self) -> pd.DataFrame | None:
        return self.frames[3] if self.frames else None


def build_report(output_path: str, allow_unmapped: bool = False, progress_cb=None,
                 cancel_event=None, *, maps=None, ageing=None, regions=None, write=None) -> BuiltReport:
    """Fetch the ageing from the ERP and write the 5-sheet workbook to
    ``output_path`` (not written while mappings are missing and
    ``allow_unmapped`` is False). The ageing is live, so it is as on today.

    A caller that builds several reports from one ageing (the Debtors Reports
    Auto Mailer) hands in what it already has: ``maps`` = (location_map,
    incharge_map), ``ageing`` = the ageing frame (or a callable returning it),
    ``regions`` = {customer -> ERP region} (or a callable), and ``write`` =
    how to write the workbook (to run it on the CPU pool)."""
    log_q = LogQueue(progress_cb)
    as_on_date = date.today()

    if maps is not None:
        location_map, incharge_map = maps
    else:
        if progress_cb:
            progress_cb(0.02, "Loading centralized mappings...")
        db = SessionLocal()
        try:
            location_map, incharge_map = mapping_store.load_all(db)
        finally:
            db.close()

    if ageing is not None:
        df = ageing() if callable(ageing) else ageing
        region_by_account = (regions() if callable(regions) else regions) or {}
    else:
        # each customer's region comes from the ERP on a connection of its own, while the ageing is fetched
        cfg = oracle_config()
        region_fetch = customer_region.RegionFetch(cfg, cancel_event)
        df = processor.fetch_ageing_from_erp(
            cfg, log_q, progress_cb=progress_cb, cancel_event=cancel_event,
            include_buckets=False,   # this report works out its own ageing buckets
        )
        region_by_account = region_fetch.result(log_q)

    missing_locations = processor.missing_location_mappings(df, location_map, region_by_account)
    df = processor.add_location_column(df, location_map, region_by_account)
    if region_by_account:
        from_erp = processor.count_rows_from_erp_region(df, region_by_account)
        log_q.put(("info", f"Location from the ERP region for {from_erp:,} of {len(df):,} rows; "
                           "the rest from the Location mappings"))

    resolved_locations = df[processor.LOCATION_COL].astype(str).str.strip()
    missing_incharges = processor.missing_incharge_mappings(
        sorted(resolved_locations.unique()), incharge_map,
    )

    validation_warnings = []
    if missing_locations:
        validation_warnings.append({"category": "Location Not Mapped", "items": missing_locations})
    if missing_incharges:
        validation_warnings.append({"category": "Accounts Incharge Not Mapped", "items": missing_incharges})

    if validation_warnings:
        for warning in validation_warnings:
            log_q.put(("warn", f"{warning['category']}: {len(warning['items'])} unmapped value(s)"))
    if validation_warnings and allow_unmapped:
        log_q.put(("warn", "Writing the report anyway - unmapped values show as -."))
    if validation_warnings and not allow_unmapped:
        log_q.put(("warn", "Fix the mappings above and regenerate - report was not written yet."))
        return BuiltReport(result={
            "needs_mapping_fix": True,
            "total_rows": int(len(df)),
            "validation_warnings": validation_warnings,
            "log": log_q.messages,
        })

    if progress_cb:
        progress_cb(0.30, "Recomputing ageing buckets...")
    df = processor.recompute_ageing_buckets(df, as_on_date, log_q)

    if progress_cb:
        progress_cb(0.45, "Building pivots...")
    df_untagged_ageing = processor.build_untagged_ageing(df)
    df_untagged_summary = processor.build_untagged_summary(df_untagged_ageing, incharge_map)
    df_below_1k_ageing = processor.build_below_1k_ageing(df)
    df_below_1k_summary = processor.build_below_1k_summary(df_below_1k_ageing, incharge_map)

    if write is None:
        processor.write_report(
            df, df_untagged_summary, df_untagged_ageing,
            df_below_1k_summary, df_below_1k_ageing,
            output_path, as_on_date, log_q=log_q, progress_cb=progress_cb,
        )
    else:
        write(df, df_untagged_summary, df_untagged_ageing, df_below_1k_summary, df_below_1k_ageing,
              output_path, as_on_date)

    filename = f"Untagged_Invoices_Report_As_On_{as_on_date.isoformat()}.xlsx"
    log_q.put(("ok", f"Done - {filename}"))

    return BuiltReport(
        result={
            "needs_mapping_fix": False,
            "allowed_unmapped": bool(validation_warnings),
            "output_path": str(output_path),
            "download_filename": filename,
            "as_on_date": as_on_date.isoformat(),
            "total_rows": int(len(df)),
            "untagged_ageing_row_count": int(len(df_untagged_ageing)),
            "untagged_summary_row_count": int(len(df_untagged_summary)),
            "below_1k_ageing_row_count": int(len(df_below_1k_ageing)),
            "below_1k_summary_row_count": int(len(df_below_1k_summary)),
            "validation_warnings": validation_warnings,
            "log": log_q.messages,
        },
        as_on_date=as_on_date,
        frames=(df, df_untagged_summary, df_untagged_ageing, df_below_1k_summary, df_below_1k_ageing),
    )
