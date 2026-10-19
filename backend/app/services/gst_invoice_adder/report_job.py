"""Build the GST Invoice Number report from the live ERP ageing.

Shared by the on-demand job (the web page) and the automatic mail job, so both
produce exactly the same workbook.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.jobs import run_cpu_phase
from app.services import erp_ageing
from app.services.gst_invoice_adder import processor


class LogQueue:
    """Collects the processor's log lines for display in the finished job."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def put(self, item) -> None:
        self.messages.append(item)


def write_workbook(output_path: str, df: pd.DataFrame, gst_map: dict):
    """100% CPU (openpyxl write), no Oracle/DB involved, so callers run it on
    the CPU process pool. Returns (total, found, blank, log messages)."""
    log_q = LogQueue()
    total, found, blank = processor.build_gst_report_pure_python(
        df, processor.COL_INV_NO, processor.COL_INV_DATE, gst_map, output_path, log_q,
    )
    return total, found, blank, log_q.messages


@dataclass
class BuiltGstReport:
    result: dict
    df: pd.DataFrame
    gst_map: dict


def build_report(output_path: str, download_name: str, progress_cb=None, cancel_event=None) -> BuiltGstReport:
    """Fetch the ageing from the ERP, look up each (invoice no, date) pair's
    GST number, and write the enriched workbook to ``output_path``. The
    Oracle fetches stay on this thread (I/O-bound, already their own
    ThreadPoolExecutor + connection pool); the CPU-bound write runs on the CPU
    process pool."""
    log_q = LogQueue()
    oracle_cfg = erp_ageing.oracle_config()

    if progress_cb:
        progress_cb(0.02, "Fetching the ageing from the ERP...")
    # the fetch reports 0.05-0.25; squeeze that into this job's 0.02-0.09
    fetch_progress = (
        (lambda frac, msg: progress_cb(0.02 + (frac - 0.05) / 0.2 * 0.07, msg)) if progress_cb else None
    )
    df = erp_ageing.fetch_ageing_from_erp(
        oracle_cfg, log_q, progress_cb=fetch_progress, cancel_event=cancel_event,
    )
    pairs_unique = processor._extract_pairs_vectorized(df, processor.COL_INV_NO, processor.COL_INV_DATE)
    log_q.put(("info", f"{len(df)} data rows found"))
    log_q.put(("info", f"{len(pairs_unique)} unique (invoice no, date) pairs"))

    if progress_cb:
        progress_cb(0.09, "Connecting to Oracle...")
    processor.init_oracle_client(oracle_cfg.instant_client_dir)
    pool = processor._make_pool(oracle_cfg, log_q)
    try:
        gst_map = processor._fetch_all_parallel(
            pairs_unique,
            pool,
            log_q,
            progress_cb,
            cancel_event=cancel_event,
        )
    finally:
        try:
            pool.close(force=bool(cancel_event is not None and cancel_event.is_set()))
        except TypeError:
            pool.close()
        except Exception:
            pass

    if progress_cb:
        progress_cb(0.90, "Preparing output workbook...")
    ok_cnt = sum(1 for v in gst_map.values() if v)
    log_q.put(("info", f"{ok_cnt}/{len(gst_map)} keys returned a GST number"))

    if progress_cb:
        progress_cb(0.92, "Writing enriched output...")
    total, found, blank, cpu_log_messages = run_cpu_phase(write_workbook, output_path, df, gst_map)
    log_q.messages.extend(cpu_log_messages)

    if progress_cb:
        progress_cb(1.0, "Done")

    return BuiltGstReport(
        result={
            "total": total,
            "found": found,
            "blank": blank,
            "output_path": output_path,
            "download_name": download_name,
            "log": log_q.messages,
        },
        df=df,
        gst_map=gst_map,
    )
