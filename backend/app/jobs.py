"""Minimal in-process background job manager.

Deliberately NOT Celery/Redis: this app must have minimal server footprint,
so a small ThreadPoolExecutor plus an in-memory job status dict is enough
for the long-running conversion/export jobs the tool routers submit.
"""

import inspect
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

# Default of 3 is sized for a modest office PC (verified against the actual
# deployment machine: 4-core i3-10100, 8GB RAM, often only ~1.5GB free once
# Windows and other software are running) - not this dev machine's specs.
# Some of these jobs process very large files (multi-hundred-MB ERP
# exports) in pandas/openpyxl, which is memory-hungry more than
# CPU-parallel-friendly; running many of them at once on 8GB total RAM risks
# thrashing the whole PC rather than helping throughput. Excel COM
# automation (win32com) is separately serialized process-wide regardless of
# this number - see app/excel_com_lock.py - so raising this does not risk
# concurrent-Excel instability. Override via JOB_POOL_WORKERS in .env if the
# actual deployment hardware has more headroom (or less).
_JOB_POOL_WORKERS = int(os.environ.get("JOB_POOL_WORKERS", "3"))
_executor = ThreadPoolExecutor(max_workers=_JOB_POOL_WORKERS)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_JOB_TTL_SECONDS = 60 * 60

logger = logging.getLogger(__name__)


def _prune_jobs(now: float) -> None:
    expired = [
        job_id
        for job_id, job in _jobs.items()
        if job["status"] in {"done", "error", "cancelled"}
        and now - job["updated_at"] > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        _jobs.pop(job_id, None)


class JobCancelled(Exception):
    """Raised cooperatively from a progress callback when cancellation was requested."""


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
            "updated_at": now,
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
        except Exception as exc:  # noqa: BLE001 - deliberately broad, job runner boundary
            logger.exception("Background job %s failed", job_id)
            with _lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = str(exc)[:500]
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
        _prune_jobs(time.monotonic())
        job = _jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            return None
        return {
            key: value
            for key, value in job.items()
            if key not in {"owner_id", "updated_at", "cancel_requested"}
        }


def cancel_job(job_id: str, *, owner_id: int) -> dict | None:
    """Request cooperative cancellation for an owned running job."""
    with _lock:
        _prune_jobs(time.monotonic())
        job = _jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            return None
        if job["status"] in {"done", "error", "cancelled"}:
            return {
                key: value
                for key, value in job.items()
                if key not in {"owner_id", "updated_at", "cancel_requested"}
            }
        job["cancel_requested"] = True
        job["phase"] = "Cancelling..."
        job["updated_at"] = time.monotonic()
        return {
            key: value
            for key, value in job.items()
            if key not in {"owner_id", "updated_at", "cancel_requested"}
        }
