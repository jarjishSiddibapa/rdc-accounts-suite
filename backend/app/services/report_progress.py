"""Shared helper for reporting progress during a large per-row Excel write
loop, used by every report writer that has one (unaccounted transactions,
pending MRN, uninvoiced expense POs, unapplied receipts, trial balance).

These writers can run against tens/hundreds of thousands of rows with no
progress feedback at all - the whole call just blocks, the same "frozen"
symptom the ERP converter had before its own progress relay was added
(see app.jobs.run_cpu_phase, which is what actually carries these calls
across the CPU-pool process boundary back to the browser).
"""


def row_progress_reporter(progress_cb, base: float, span: float, total_rows: int):
    """Build a cheap per-row callback that reports progress at most ~200
    times across a data-row loop, scaled into [base, base + span].

    Calling progress_cb every single row would be wasteful (each call
    crosses a process boundary via a queue put); a ~200-step stride keeps
    the UI moving smoothly without that overhead.
    """
    if not progress_cb or total_rows <= 0:
        return lambda _rows_written: None
    step = max(1, total_rows // 200)

    def _report(rows_written: int) -> None:
        if rows_written % step == 0 or rows_written == total_rows:
            progress_cb(base + span * (rows_written / total_rows), "Writing workbook...")

    return _report
