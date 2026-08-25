"""Minimal in-process background job manager.

Deliberately NOT Celery/Redis: this app must have minimal server footprint,
so a small ThreadPoolExecutor plus an in-memory job status dict is enough
for the long-running conversion/export jobs the tool routers submit.
"""

import asyncio
import atexit
import inspect
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

# Default of 3 is sized for a modest office PC (verified against the actual
# deployment machine: 4-core i3-10100, 8GB RAM, often only ~1.5GB free once
# Windows and other software are running) - not this dev machine's specs.
# Some of these jobs process very large files (multi-hundred-MB ERP
# exports) in pandas/openpyxl, which is memory-hungry more than
# CPU-parallel-friendly; running many of them at once on 8GB total RAM risks
# thrashing the whole PC rather than helping throughput. Override via
# JOB_POOL_WORKERS in .env if the actual deployment hardware has more
# headroom (or less).
_JOB_POOL_WORKERS = int(os.environ.get("JOB_POOL_WORKERS", "3"))
_executor = ThreadPoolExecutor(max_workers=_JOB_POOL_WORKERS)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_JOB_TTL_SECONDS = 60 * 60
_PRIVATE_JOB_KEYS = {
    "owner_id",
    "updated_at",
    "cancel_requested",
    "started_at",
    "_actions",
}

# A job still marked "running" past this long is almost certainly hung
# (crashed worker, stuck external call, a genuine bug) rather than legitimate
# work in progress - reported to the user as a timeout instead of spinning
# forever. This does NOT forcibly kill the underlying thread/process (Python
# offers no safe way to do that without tearing down the whole pool) - it
# only stops the *user* from waiting forever; the stuck work itself still
# runs to completion (or crashes) in the background, its result discarded.
_JOB_MAX_SECONDS = int(os.environ.get("JOB_MAX_SECONDS", str(45 * 60)))

logger = logging.getLogger(__name__)


class _NoopLogQueue:
    """Drop-in substitute for the log_q objects several processors' progress
    checkpoints call .put((level, message)) on, for use when running a
    processing phase on the CPU process pool: the real progress/log
    reporting for these phases already happens via progress_cb calls on the
    calling thread immediately before/after the whole phase runs (see
    run_cpu_phase), so the granular per-line log_q messages a phase would
    normally emit mid-run are simply not collected while it's running in a
    subprocess - passing None instead would crash the first log_q.put(...)
    call, since none of these processors guard against a None log_q."""

    def put(self, item) -> None:
        pass


def run_cpu_phase(fn, *args, **kwargs):
    """Run one CPU-bound processing phase on a separate OS process instead
    of the calling job's own thread, for real multi-core throughput (Python
    threads can't do this for CPU-bound work - the GIL serializes them onto
    one core's worth of time no matter how many threads are running).

    Blocks the calling thread until done, so call this from inside a job
    body exactly where you'd otherwise call the CPU-heavy function directly;
    progress_cb calls immediately before/after (as every job body already
    does around today's in-thread calls) give the same progress granularity
    as before - none of these phases report progress *within* one call.

    fn and every arg/kwarg MUST be picklable: plain functions defined at
    module level (not closures/lambdas), and plain data (str, int, float,
    dict, list, DataFrame) - never a live DB session, open file handle,
    thread lock, or log_q object (use _NoopLogQueue for that last one)."""
    future = _get_cpu_executor().submit(fn, *args, **kwargs)
    return future.result()


async def run_cpu_phase_async(fn, *args, **kwargs):
    """Same as run_cpu_phase, but awaitable from inside an `async def`
    FastAPI route handler instead of a background job's own thread -
    `run_cpu_phase`'s blocking future.result() would freeze the whole
    event loop (and every other request being served by this process)
    while waiting, since there's no separate thread to block here."""
    future = _get_cpu_executor().submit(fn, *args, **kwargs)
    return await asyncio.wrap_future(future)


# Worker count is RAM-budgeted, not just CPU-count-budgeted, mirroring
# app/services/rdc_payables/processor.py's own nested process pool (the one
# other place in this app already does this, in production) - each worker
# process pays ~250MB to import pandas/numpy/openpyxl on top of whatever
# it's holding, and the deployment box (4-core i3-10100, 8GB RAM) only
# guarantees ~1.5GB free at the low end. The reserve accounts for the main
# process itself AND rdc_payables' own nested pool at its own worst case
# (both can be alive on this box at the same time), then hard-caps at 2
# regardless of what the math alone would allow - deliberately
# conservative, since this pool is shared by every OTHER CPU-bound tool in
# the suite at once, not just one. Override via CPU_JOB_POOL_WORKERS in .env
# for different deployment hardware.
_CPU_MIN_AVAILABLE_RAM_MB = 1536
_CPU_RESERVED_FOR_MAIN_AND_RDC_MB = 400 + 4 * 250
_CPU_PER_WORKER_RAM_MB = 250


def _default_cpu_pool_workers() -> int:
    cpu_cap = max(1, (os.cpu_count() or 1) - 1)  # leave 1 core for the event loop/HTTP handling
    ram_cap = max(
        1,
        (_CPU_MIN_AVAILABLE_RAM_MB - _CPU_RESERVED_FOR_MAIN_AND_RDC_MB) // _CPU_PER_WORKER_RAM_MB,
    )
    return max(1, min(cpu_cap, ram_cap, 2))


_CPU_POOL_WORKERS = int(os.environ.get("CPU_JOB_POOL_WORKERS", str(_default_cpu_pool_workers())))

# Lazily created, like rdc_payables' own pool - a fresh worker process only
# gets spawned (paying the cost of importing pandas/openpyxl etc.) the first
# time a CPU-bound phase actually runs, not on every server start whether or
# not any CPU-heavy tool is ever used in that run.
_cpu_executor: ProcessPoolExecutor | None = None
_cpu_executor_lock = threading.Lock()


def _get_cpu_executor() -> ProcessPoolExecutor:
    global _cpu_executor
    if _cpu_executor is None:
        with _cpu_executor_lock:
            if _cpu_executor is None:
                _cpu_executor = ProcessPoolExecutor(max_workers=_CPU_POOL_WORKERS)
                atexit.register(_cpu_executor.shutdown, wait=False, cancel_futures=True)
    return _cpu_executor


def _prune_jobs(now: float) -> None:
    expired = [
        job_id
        for job_id, job in _jobs.items()
        if job["status"] in {"done", "error", "cancelled"}
        and now - job["updated_at"] > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        _jobs.pop(job_id, None)


def _check_hung_jobs(now: float) -> None:
    """Mark any job that's been "running" for longer than _JOB_MAX_SECONDS
    as failed with a timeout message, so the user isn't left staring at a
    spinner forever. Does not (and cannot) stop the underlying thread/process
    itself - see _JOB_MAX_SECONDS' docstring."""
    for job in _jobs.values():
        if job["status"] == "running" and now - job["started_at"] > _JOB_MAX_SECONDS:
            job["status"] = "error"
            job["error"] = (
                "This job exceeded the maximum allowed run time and was marked as failed."
            )
            job["updated_at"] = now


class JobCancelled(Exception):
    """Raised cooperatively from a progress callback when cancellation was requested."""


class JobUserError(Exception):
    """Raise this from a job body for an expected, user-actionable failure
    (bad file format, missing sheet/column, no data rows, etc.) - its
    message is safe to show to the user verbatim. Any other exception is
    treated as an internal bug: logged in full server-side, but the user
    only ever sees a generic message, never a raw Python exception string."""


def _public_job(job: dict) -> dict:
    """Return the client-safe portion of a job record.

    Action claims are deliberately private: they coordinate irreversible
    operations such as sending email, but are not part of the job-status API.
    """
    return {key: value for key, value in job.items() if key not in _PRIVATE_JOB_KEYS}


def _update_progress(job_id: str, frac: float, phase: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            if job.get("cancel_requested"):
                raise JobCancelled("Job cancelled by user")
            job["progress"] = frac
            job["phase"] = phase
            job["updated_at"] = time.monotonic()


def submit_job(fn, *args, owner_id: int, **kwargs) -> str:
    job_id = str(uuid.uuid4())
    now = time.monotonic()
    with _lock:
        _prune_jobs(now)
        _jobs[job_id] = {
            "owner_id": owner_id,
            "status": "running",
            "progress": 0.0,
            "phase": "",
            "result": None,
            "error": None,
            "cancel_requested": False,
            "_actions": {},
            "updated_at": now,
            "started_at": now,
        }

    accepts_progress_cb = "progress_cb" in inspect.signature(fn).parameters

    def _run():
        try:
            if accepts_progress_cb:
                result = fn(
                    *args,
                    progress_cb=lambda frac, phase: _update_progress(job_id, frac, phase),
                    **kwargs,
                )
            else:
                result = fn(*args, **kwargs)
        except JobCancelled:
            with _lock:
                job = _jobs.get(job_id)
                if job is not None:
                    job["status"] = "cancelled"
                    job["phase"] = "Cancelled"
                    job["error"] = None
                    job["updated_at"] = time.monotonic()
            return
        except JobUserError as exc:
            # Expected, user-actionable failure - the message was written
            # for the user, so show it verbatim.
            with _lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = str(exc)[:500]
                _jobs[job_id]["updated_at"] = time.monotonic()
            return
        except Exception as exc:  # noqa: BLE001 - deliberately broad, job runner boundary
            logger.exception("Background job %s failed", job_id)
            with _lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = "An internal error occurred. Check the server logs or contact support."
                _jobs[job_id]["updated_at"] = time.monotonic()
            return

        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["phase"] = "Cancelled"
            else:
                job["status"] = "done"
                job["result"] = result
            job["updated_at"] = time.monotonic()

    _executor.submit(_run)
    return job_id


def get_job(job_id: str, *, owner_id: int) -> dict | None:
    with _lock:
        now = time.monotonic()
        _prune_jobs(now)
        _check_hung_jobs(now)
        job = _jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            return None
        return _public_job(job)


def cancel_job(job_id: str, *, owner_id: int) -> dict | None:
    """Request cooperative cancellation for an owned running job."""
    with _lock:
        now = time.monotonic()
        _prune_jobs(now)
        _check_hung_jobs(now)
        job = _jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            return None
        if job["status"] in {"done", "error", "cancelled"}:
            return _public_job(job)
        job["cancel_requested"] = True
        job["phase"] = "Cancelling..."
        job["updated_at"] = time.monotonic()
        return _public_job(job)


def claim_job_action(job_id: str, *, owner_id: int, action: str) -> tuple[str, dict | None]:
    """Atomically claim a one-shot action associated with an owned job.

    This closes the same-user/multiple-tab race where both tabs can inspect
    the same completed preview and then trigger the same irreversible action
    (most importantly email delivery).  The returned state is one of:
    ``claimed``, ``in_progress``, ``completed``, ``failed``, or ``missing``.
    """
    with _lock:
        now = time.monotonic()
        _prune_jobs(now)
        _check_hung_jobs(now)
        job = _jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            return "missing", None

        actions = job.setdefault("_actions", {})
        existing = actions.get(action)
        if existing is not None:
            return existing, _public_job(job)

        actions[action] = "in_progress"
        job["updated_at"] = now
        return "claimed", _public_job(job)


def finish_job_action(
    job_id: str,
    *,
    owner_id: int,
    action: str,
    succeeded: bool,
) -> bool:
    """Finish an action claim without allowing it to be claimed again.

    Failed email attempts remain failed rather than becoming retryable because
    a transport failure can be ambiguous: the server may have accepted some
    messages before the error reached us.  A fresh preview is therefore the
    safe retry path.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            return False
        actions = job.setdefault("_actions", {})
        if actions.get(action) != "in_progress":
            return False
        actions[action] = "completed" if succeeded else "failed"
        job["updated_at"] = time.monotonic()
        return True
