"""Durable MySQL-backed background jobs and dedicated worker runtime.

API processes only enqueue and inspect work. Separate ``app.worker``
processes atomically lease queued rows, execute registered module-level job
functions, heartbeat their leases, and persist progress/results. Any API
worker can therefore serve any browser tab without sticky sessions.
"""

from __future__ import annotations

import asyncio
import atexit
import importlib
import inspect
import json
import logging
import multiprocessing
import os
import queue as queue_module
import threading
import time
import uuid
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, OperationalError

from app import config
from app.client_context import current_tab_id, normalize_tab_id
from app.database import SessionLocal
from app.models import BackgroundJob, BackgroundJobAction, BackgroundResourceSlot

logger = logging.getLogger(__name__)

_JOB_TTL_SECONDS = 60 * 60
_inline_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="test-inline-job")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

# Only these server-owned module-level functions can be resolved from a
# database row. A user-controlled value can never turn into an import/call.
_ALLOWED_TASKS = frozenset(
    {
        "app.routers.erp_converter:_job_convert",
        "app.routers.gstr2b:_run_combine_job",
        "app.routers.closing_period:_run_combine_job",
        "app.routers.creditors_ageing:_run_process_job",
        "app.routers.trial_balance_formatter:_run_process_job",
        "app.services.iocl_balance.monitor:run_check_job",
        "app.routers.gst_invoice_adder:_job_enrich",
        "app.routers.rdc_payables:_run_process_job",
        "app.routers.trial_balance:_run_process_job_from_stash",
        "app.routers.unaccounted_txn:_job_unaccounted",
        "app.routers.unaccounted_txn:_job_mrn",
        "app.routers.unaccounted_txn:_job_po",
        "app.routers.unaccounted_txn:_job_mail_send",
        "app.routers.unapplied_receipts:_run_process_job",
        "app.routers.ultrafine_balance_confirmation:_job_preview",
        "app.routers.ultrafine_balance_confirmation:_job_send",
        "app.routers.ultrafine_payment_reminder:_job_preview",
        "app.routers.ultrafine_payment_reminder:_job_send",
    }
)

_RESOURCE_BY_TASK = {
    "app.routers.gst_invoice_adder:_job_enrich": "oracle-gst",
}

# Dispatch jobs are intentionally server-owned once accepted. Cancelling one
# because a browser vanished could send only part of a customer email batch.
_DETACHED_TASKS = frozenset(
    {
        "app.routers.unaccounted_txn:_job_mail_send",
        "app.routers.ultrafine_balance_confirmation:_job_send",
        "app.routers.ultrafine_payment_reminder:_job_send",
        "app.services.iocl_balance.monitor:run_check_job",
    }
)


class _NoopLogQueue:
    def put(self, item) -> None:
        pass


class JobCancelled(Exception):
    """Raised cooperatively when a persisted cancellation is observed."""


class JobUserError(Exception):
    """Expected failure whose message is safe to show to the user."""


# CPU phases remain process-based inside each dedicated job worker. On the
# documented 8 GB deployment host this defaults to one child per worker.
_CPU_MIN_AVAILABLE_RAM_MB = 1536
_CPU_RESERVED_FOR_MAIN_AND_RDC_MB = 400 + 4 * 250
_CPU_PER_WORKER_RAM_MB = 250


def _default_cpu_pool_workers() -> int:
    cpu_cap = max(1, (os.cpu_count() or 1) - 1)
    ram_cap = max(
        1,
        (_CPU_MIN_AVAILABLE_RAM_MB - _CPU_RESERVED_FOR_MAIN_AND_RDC_MB)
        // _CPU_PER_WORKER_RAM_MB,
    )
    return max(1, min(cpu_cap, ram_cap, 2))


_CPU_POOL_WORKERS = int(
    os.environ.get("CPU_JOB_POOL_WORKERS", str(_default_cpu_pool_workers()))
)
_cpu_executor: ProcessPoolExecutor | None = None
_cpu_executor_lock = threading.Lock()
_execution_context = threading.local()


def _get_cpu_executor() -> ProcessPoolExecutor:
    global _cpu_executor
    if _cpu_executor is None:
        with _cpu_executor_lock:
            if _cpu_executor is None:
                _cpu_executor = ProcessPoolExecutor(max_workers=_CPU_POOL_WORKERS)
                atexit.register(
                    _cpu_executor.shutdown,
                    wait=False,
                    cancel_futures=True,
                )
    return _cpu_executor


_progress_manager: multiprocessing.managers.SyncManager | None = None
_progress_manager_lock = threading.Lock()


def _get_progress_manager() -> multiprocessing.managers.SyncManager:
    """Lazily started, reused for the life of the worker process. A
    ``Manager`` runs its own tiny helper process and proxies picklable
    ``Queue`` objects into/out of the CPU pool's worker processes - the
    only way to relay progress across a ``ProcessPoolExecutor`` boundary
    without changing that pool's own worker startup.
    """
    global _progress_manager
    if _progress_manager is None:
        with _progress_manager_lock:
            if _progress_manager is None:
                _progress_manager = multiprocessing.Manager()
                atexit.register(_progress_manager.shutdown)
    return _progress_manager


class _QueueProgress:
    """Picklable stand-in for a plain ``progress_cb(frac, phase)`` callable
    that instead posts updates onto a cross-process queue. Passed into the
    CPU pool worker in place of the real callback, which cannot itself
    cross the process boundary."""

    def __init__(self, queue):
        self._queue = queue

    def __call__(self, frac, phase) -> None:
        try:
            self._queue.put_nowait((frac, phase))
        except Exception:
            pass


def _drain_progress(queue, progress_cb) -> None:
    """Apply only the most recent queued update - intermediate ones are
    superseded and not worth a separate `_update_progress` DB write each."""
    latest = None
    while True:
        try:
            latest = queue.get_nowait()
        except (queue_module.Empty, EOFError, OSError):
            break
    if latest is not None:
        try:
            progress_cb(*latest)
        except Exception:
            logger.exception("progress_cb failed while draining CPU phase progress")


def _fn_accepts_progress_cb(fn) -> bool:
    try:
        return "progress_cb" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _abort_cpu_executor() -> None:
    """Terminate the current worker's CPU children after client cancellation.

    ``Future.cancel()`` cannot stop work already executing in another process.
    Each durable worker runs only one background job at a time, so terminating
    that worker's private CPU pool cannot affect another user's job. A fresh
    pool is created lazily for the next claimed job.
    """
    global _cpu_executor
    with _cpu_executor_lock:
        executor = _cpu_executor
        _cpu_executor = None
        if executor is None:
            return
        processes = list((getattr(executor, "_processes", None) or {}).values())
        executor.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=1)


def _current_cancel_event() -> threading.Event | None:
    return getattr(_execution_context, "cancel_event", None)


def run_cpu_phase(fn, *args, progress_cb=None, **kwargs):
    """Run ``fn`` in the CPU process pool and block until it finishes.

    When the caller passes ``progress_cb`` *and* ``fn`` itself declares a
    ``progress_cb`` parameter, a cross-process queue is wired in its place
    so ``fn``'s own fine-grained progress reporting (bytes parsed, rows
    written, ...) reaches the caller's callback in near-real time instead
    of the phase appearing frozen until the whole call returns. Callers
    that omit ``progress_cb`` see no behavioural change.
    """
    queue = None
    if progress_cb is not None and _fn_accepts_progress_cb(fn):
        queue = _get_progress_manager().Queue()
        kwargs = {**kwargs, "progress_cb": _QueueProgress(queue)}
    future = _get_cpu_executor().submit(fn, *args, **kwargs)
    while True:
        try:
            result = future.result(timeout=0.25)
        except FutureTimeoutError:
            if queue is not None:
                _drain_progress(queue, progress_cb)
            cancel_event = _current_cancel_event()
            if cancel_event is not None and cancel_event.is_set():
                _abort_cpu_executor()
                raise JobCancelled("Job cancelled after browser tab closed")
            continue
        except Exception as exc:
            cancel_event = _current_cancel_event()
            if cancel_event is not None and cancel_event.is_set():
                _abort_cpu_executor()
                raise JobCancelled("Job cancelled after browser tab closed") from exc
            raise
        if queue is not None:
            _drain_progress(queue, progress_cb)
        return result


async def run_cpu_phase_async(fn, *args, **kwargs):
    future = _get_cpu_executor().submit(fn, *args, **kwargs)
    wrapped = asyncio.wrap_future(future)
    while True:
        done, _pending = await asyncio.wait({wrapped}, timeout=0.25)
        if done:
            try:
                return next(iter(done)).result()
            except Exception as exc:
                cancel_event = _current_cancel_event()
                if cancel_event is not None and cancel_event.is_set():
                    _abort_cpu_executor()
                    raise JobCancelled("Job cancelled after browser tab closed") from exc
                raise
        cancel_event = _current_cancel_event()
        if cancel_event is not None and cancel_event.is_set():
            _abort_cpu_executor()
            raise JobCancelled("Job cancelled after browser tab closed")


def _encode_value(value: Any) -> Any:
    """Lossless JSON-safe encoding for server-constructed task payloads."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return {"__job_type__": "path", "value": str(value)}
    if isinstance(value, datetime):
        return {"__job_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__job_type__": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__job_type__": "decimal", "value": str(value)}
    if isinstance(value, set):
        return {"__job_type__": "set", "items": [_encode_value(v) for v in value]}
    if isinstance(value, tuple):
        return {"__job_type__": "tuple", "items": [_encode_value(v) for v in value]}
    if isinstance(value, list):
        return [_encode_value(v) for v in value]
    if isinstance(value, dict):
        return {
            "__job_type__": "dict",
            "items": [[_encode_value(k), _encode_value(v)] for k, v in value.items()],
        }
    if is_dataclass(value):
        return _encode_value(asdict(value))
    if value.__class__.__module__.startswith("numpy") and hasattr(value, "item"):
        return _encode_value(value.item())
    if hasattr(value, "isoformat"):
        return {"__job_type__": "datetime", "value": value.isoformat()}
    raise TypeError(f"Unsupported durable job value: {type(value).__name__}")


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_value(v) for v in value]
    if not isinstance(value, dict) or "__job_type__" not in value:
        return value
    kind = value["__job_type__"]
    if kind == "path":
        return Path(value["value"])
    if kind == "datetime":
        return datetime.fromisoformat(value["value"])
    if kind == "date":
        return date.fromisoformat(value["value"])
    if kind == "decimal":
        return Decimal(value["value"])
    if kind == "set":
        return {_decode_value(v) for v in value["items"]}
    if kind == "tuple":
        return tuple(_decode_value(v) for v in value["items"])
    if kind == "dict":
        return {_decode_value(k): _decode_value(v) for k, v in value["items"]}
    raise ValueError(f"Unknown durable job value type: {kind}")


def dumps_value(value: Any) -> str:
    return json.dumps(_encode_value(value), ensure_ascii=False, separators=(",", ":"))


def loads_value(raw: str | None) -> Any:
    return None if raw is None else _decode_value(json.loads(raw))


def _task_name(fn: Callable) -> str:
    name = f"{fn.__module__}:{fn.__qualname__}"
    if "<locals>" in name or name not in _ALLOWED_TASKS:
        raise ValueError(
            "Background jobs must use a registered module-level task function."
        )
    return name


def _resolve_task(task_name: str) -> Callable:
    if task_name not in _ALLOWED_TASKS:
        raise RuntimeError(f"Refusing unregistered background task {task_name!r}")
    module_name, function_name = task_name.split(":", 1)
    fn = getattr(importlib.import_module(module_name), function_name)
    if not callable(fn):
        raise RuntimeError(f"Registered background task {task_name!r} is not callable")
    return fn


def _public_job(job: BackgroundJob) -> dict:
    # Queued is exposed as running so existing frontend polling logic and all
    # desktop-parity flows keep the same API contract.
    public_status = "running" if job.status == "queued" else job.status
    return {
        "status": public_status,
        "progress": float(job.progress or 0.0),
        "phase": job.phase or ("Queued" if job.status == "queued" else ""),
        "result": loads_value(job.result_json),
        "error": job.error,
    }


def submit_job(
    fn,
    *args,
    owner_id: int,
    client_tab_id: str | None = None,
    cancel_on_disconnect: bool | None = None,
    **kwargs,
) -> str:
    task_name = _task_name(fn)
    job_id = str(uuid.uuid4())
    now = _utcnow()
    tab_id = normalize_tab_id(client_tab_id) or current_tab_id()
    should_cancel_on_disconnect = (
        task_name not in _DETACHED_TASKS
        if cancel_on_disconnect is None
        else bool(cancel_on_disconnect)
    )
    # Calls outside an HTTP request (tests, scheduler-owned work, maintenance)
    # deliberately remain detached rather than expiring without a browser.
    should_cancel_on_disconnect = bool(should_cancel_on_disconnect and tab_id)
    db = SessionLocal()
    try:
        db.add(
            BackgroundJob(
                id=job_id,
                owner_id=owner_id,
                task_name=task_name,
                args_json=dumps_value(list(args)),
                kwargs_json=dumps_value(kwargs),
                resource_key=_RESOURCE_BY_TASK.get(task_name),
                client_tab_id=tab_id,
                client_heartbeat_at=now if should_cancel_on_disconnect else None,
                cancel_on_disconnect=should_cancel_on_disconnect,
                status="queued",
                progress=0.0,
                phase="Queued",
                priority=100,
                attempts=0,
                created_at=now,
                updated_at=now,
                is_deleted=False,
            )
        )
        db.commit()
        return job_id
    finally:
        db.close()


def get_job(
    job_id: str,
    *,
    owner_id: int,
    client_tab_id: str | None = None,
) -> dict | None:
    tab_id = normalize_tab_id(client_tab_id) or current_tab_id()
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.owner_id == owner_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .first()
        )
        if (
            job is not None
            and job.cancel_on_disconnect
            and tab_id
            and job.client_tab_id == tab_id
            and job.status in {"queued", "running"}
            and not job.cancel_requested
        ):
            job.client_heartbeat_at = _utcnow()
            db.commit()
        return None if job is None else _public_job(job)
    finally:
        db.close()


def cancel_job(job_id: str, *, owner_id: int) -> dict | None:
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.owner_id == owner_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .with_for_update()
            .first()
        )
        if job is None:
            return None
        if job.status == "queued":
            now = _utcnow()
            job.status = "cancelled"
            job.phase = "Cancelled"
            job.cancel_requested = True
            job.finished_at = now
            job.updated_at = now
        elif job.status == "running":
            job.cancel_requested = True
            job.phase = "Cancelling..."
            job.updated_at = _utcnow()
        db.commit()
        return _public_job(job)
    finally:
        db.close()


def abandon_job(
    job_id: str,
    *,
    owner_id: int,
    client_tab_id: str,
) -> dict | None:
    """Cancel only work owned by the closing browser tab.

    Owner scoping protects users from one another; the tab match additionally
    prevents one of the same user's tabs from abandoning a sibling tab's job.
    Detached dispatch jobs acknowledge the lifecycle request but continue.
    """
    tab_id = normalize_tab_id(client_tab_id)
    if tab_id is None:
        return None
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.owner_id == owner_id,
                BackgroundJob.client_tab_id == tab_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .with_for_update()
            .first()
        )
        if job is None:
            return None
        if not job.cancel_on_disconnect or job.status not in {"queued", "running"}:
            return _public_job(job)
        now = _utcnow()
        job.cancel_requested = True
        job.updated_at = now
        if job.status == "queued":
            job.status = "cancelled"
            job.phase = "Cancelled because the browser tab closed"
            job.finished_at = now
        else:
            job.phase = "Stopping because the browser tab closed..."
        db.commit()
        return _public_job(job)
    finally:
        db.close()


def claim_job_action(job_id: str, *, owner_id: int, action: str) -> tuple[str, dict | None]:
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.owner_id == owner_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .first()
        )
        if job is None:
            return "missing", None
        existing = (
            db.query(BackgroundJobAction)
            .filter(
                BackgroundJobAction.job_id == job_id,
                BackgroundJobAction.action == action,
                BackgroundJobAction.is_deleted.is_(False),
            )
            .first()
        )
        if existing is not None:
            return existing.status, _public_job(job)
        now = _utcnow()
        db.add(
            BackgroundJobAction(
                job_id=job_id,
                owner_id=owner_id,
                action=action,
                status="in_progress",
                created_at=now,
                updated_at=now,
                is_deleted=False,
            )
        )
        try:
            db.commit()
            return "claimed", _public_job(job)
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(BackgroundJobAction)
                .filter(
                    BackgroundJobAction.job_id == job_id,
                    BackgroundJobAction.action == action,
                    BackgroundJobAction.is_deleted.is_(False),
                )
                .first()
            )
            return (existing.status if existing else "in_progress"), _public_job(job)
    finally:
        db.close()


def finish_job_action(
    job_id: str,
    *,
    owner_id: int,
    action: str,
    succeeded: bool,
) -> bool:
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobAction)
            .filter(
                BackgroundJobAction.job_id == job_id,
                BackgroundJobAction.owner_id == owner_id,
                BackgroundJobAction.action == action,
                BackgroundJobAction.status == "in_progress",
                BackgroundJobAction.is_deleted.is_(False),
            )
            .with_for_update()
            .first()
        )
        if row is None:
            return False
        row.status = "completed" if succeeded else "failed"
        row.updated_at = _utcnow()
        db.commit()
        return True
    finally:
        db.close()


def _client_lease_expired(job: BackgroundJob, now: datetime) -> bool:
    if not job.cancel_on_disconnect or not job.client_tab_id:
        return False
    if job.client_heartbeat_at is None:
        return True
    cutoff = now - timedelta(seconds=config.JOB_CLIENT_HEARTBEAT_TIMEOUT_SECONDS)
    return job.client_heartbeat_at < cutoff


def _cancel_expired_queued_jobs(db, now: datetime) -> None:
    cutoff = now - timedelta(seconds=config.JOB_CLIENT_HEARTBEAT_TIMEOUT_SECONDS)
    stale = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.status == "queued",
            BackgroundJob.cancel_requested.is_(False),
            BackgroundJob.cancel_on_disconnect.is_(True),
            BackgroundJob.client_tab_id.isnot(None),
            or_(
                BackgroundJob.client_heartbeat_at.is_(None),
                BackgroundJob.client_heartbeat_at < cutoff,
            ),
            BackgroundJob.is_deleted.is_(False),
        )
        .with_for_update(skip_locked=True)
        .limit(100)
        .all()
    )
    for job in stale:
        job.status = "cancelled"
        job.cancel_requested = True
        job.phase = "Cancelled after the browser tab disconnected"
        job.finished_at = now
        job.updated_at = now


def _recover_stale_jobs(db, now: datetime) -> None:
    stale = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.status == "running",
            BackgroundJob.lease_expires_at.isnot(None),
            BackgroundJob.lease_expires_at < now,
            BackgroundJob.is_deleted.is_(False),
        )
        .with_for_update(skip_locked=True)
        .limit(20)
        .all()
    )
    for job in stale:
        (
            db.query(BackgroundResourceSlot)
            .filter(
                BackgroundResourceSlot.job_id == job.id,
                BackgroundResourceSlot.is_deleted.is_(False),
            )
            .update(
                {
                    BackgroundResourceSlot.job_id: None,
                    BackgroundResourceSlot.lease_owner: None,
                    BackgroundResourceSlot.lease_expires_at: None,
                    BackgroundResourceSlot.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.updated_at = now
        if job.cancel_requested:
            job.status = "cancelled"
            job.phase = "Cancelled"
            job.finished_at = now
        elif job.attempts >= config.JOB_MAX_ATTEMPTS:
            job.status = "error"
            job.error = "The processing worker stopped repeatedly. Please run the report again."
            job.finished_at = now
        else:
            job.status = "queued"
            job.phase = "Queued after worker recovery"
            job.not_before = now + timedelta(seconds=2)


def _claim_next_job_once(worker_id: str) -> str | None:
    now = _utcnow()
    db = SessionLocal()
    try:
        _cancel_expired_queued_jobs(db, now)
        _recover_stale_jobs(db, now)
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.status == "queued",
                BackgroundJob.cancel_requested.is_(False),
                BackgroundJob.is_deleted.is_(False),
                BackgroundJob.attempts < config.JOB_MAX_ATTEMPTS,
                or_(BackgroundJob.not_before.is_(None), BackgroundJob.not_before <= now),
            )
            .order_by(BackgroundJob.priority.asc(), BackgroundJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            db.commit()
            return None

        lease_until = now + timedelta(seconds=config.JOB_LEASE_SECONDS)
        if job.resource_key:
            slot = (
                db.query(BackgroundResourceSlot)
                .filter(
                    BackgroundResourceSlot.resource_key == job.resource_key,
                    BackgroundResourceSlot.is_deleted.is_(False),
                    or_(
                        BackgroundResourceSlot.job_id.is_(None),
                        BackgroundResourceSlot.lease_expires_at < now,
                    ),
                )
                .order_by(BackgroundResourceSlot.slot_number.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
            if slot is None:
                job.phase = f"Waiting for {job.resource_key} capacity"
                job.not_before = now + timedelta(seconds=2)
                job.updated_at = now
                db.commit()
                return None
            slot.job_id = job.id
            slot.lease_owner = worker_id
            slot.lease_expires_at = lease_until
            slot.updated_at = now

        job.status = "running"
        job.phase = "Starting..."
        job.lease_owner = worker_id
        job.lease_expires_at = lease_until
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        job.updated_at = now
        job.not_before = None
        job.attempts += 1
        db.commit()
        return job.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _is_retryable_mysql_lock_error(exc: OperationalError) -> bool:
    original = getattr(exc, "orig", None)
    error_args = getattr(original, "args", ())
    return bool(error_args) and error_args[0] in {1205, 1213}


def claim_next_job(worker_id: str) -> str | None:
    """Atomically lease one job, retrying transient MySQL lock conflicts."""
    for attempt in range(4):
        try:
            return _claim_next_job_once(worker_id)
        except OperationalError as exc:
            if not _is_retryable_mysql_lock_error(exc) or attempt == 3:
                raise
            time.sleep(0.025 * (attempt + 1))
    return None


def _heartbeat(job_id: str, worker_id: str) -> bool:
    now = _utcnow()
    lease_until = now + timedelta(seconds=config.JOB_LEASE_SECONDS)
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.status == "running",
                BackgroundJob.lease_owner == worker_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .first()
        )
        if job is None:
            return False
        if _client_lease_expired(job, now):
            job.cancel_requested = True
            job.phase = "Stopping after the browser tab disconnected..."
        job.heartbeat_at = now
        job.lease_expires_at = lease_until
        job.updated_at = now
        (
            db.query(BackgroundResourceSlot)
            .filter(
                BackgroundResourceSlot.job_id == job_id,
                BackgroundResourceSlot.lease_owner == worker_id,
                BackgroundResourceSlot.is_deleted.is_(False),
            )
            .update(
                {
                    BackgroundResourceSlot.lease_expires_at: lease_until,
                    BackgroundResourceSlot.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        cancelled = bool(job.cancel_requested)
        db.commit()
        return not cancelled
    finally:
        db.close()


def _job_should_continue(job_id: str, worker_id: str) -> bool:
    """Fast cancellation check used between durable lease heartbeats."""
    now = _utcnow()
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.status == "running",
                BackgroundJob.lease_owner == worker_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .first()
        )
        if job is None:
            return False
        if _client_lease_expired(job, now):
            job.cancel_requested = True
            job.phase = "Stopping after the browser tab disconnected..."
            job.updated_at = now
        should_continue = not bool(job.cancel_requested)
        db.commit()
        return should_continue
    finally:
        db.close()


def _update_progress(job_id: str, worker_id: str, frac: float, phase: str) -> None:
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.status == "running",
                BackgroundJob.lease_owner == worker_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .first()
        )
        if job is None or job.cancel_requested:
            raise JobCancelled("Job cancelled by user")
        job.progress = max(0.0, min(1.0, float(frac)))
        job.phase = str(phase)[:255]
        job.updated_at = _utcnow()
        db.commit()
    finally:
        db.close()


def _finish_job(
    job_id: str,
    worker_id: str,
    *,
    status: str,
    result: Any = None,
    error: str | None = None,
    phase: str | None = None,
) -> None:
    now = _utcnow()
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.lease_owner == worker_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .with_for_update()
            .first()
        )
        if job is None:
            logger.warning("Worker %s lost the lease for job %s", worker_id, job_id)
            return
        if job.cancel_requested and status == "done":
            status = "cancelled"
            result = None
            phase = "Cancelled"
        job.status = status
        job.result_json = dumps_value(result) if status == "done" else None
        job.error = error
        job.phase = phase or ("Done" if status == "done" else job.phase)
        job.progress = 1.0 if status == "done" else job.progress
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        (
            db.query(BackgroundResourceSlot)
            .filter(
                BackgroundResourceSlot.job_id == job_id,
                BackgroundResourceSlot.is_deleted.is_(False),
            )
            .update(
                {
                    BackgroundResourceSlot.job_id: None,
                    BackgroundResourceSlot.lease_owner: None,
                    BackgroundResourceSlot.lease_expires_at: None,
                    BackgroundResourceSlot.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        db.commit()
    finally:
        db.close()


def execute_job(job_id: str, worker_id: str) -> None:
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.id == job_id,
                BackgroundJob.status == "running",
                BackgroundJob.lease_owner == worker_id,
                BackgroundJob.is_deleted.is_(False),
            )
            .first()
        )
        if job is None:
            return
        task_name = job.task_name
        args = loads_value(job.args_json)
        kwargs = loads_value(job.kwargs_json)
    finally:
        db.close()

    stop_heartbeat = threading.Event()
    cancel_event = threading.Event()

    def heartbeat_loop() -> None:
        while not stop_heartbeat.wait(config.JOB_HEARTBEAT_SECONDS):
            try:
                if not _heartbeat(job_id, worker_id):
                    cancel_event.set()
                    return
            except Exception:
                logger.exception("Heartbeat failed for job %s", job_id)

    def client_monitor_loop() -> None:
        while not stop_heartbeat.wait(config.JOB_CLIENT_MONITOR_SECONDS):
            try:
                if not _job_should_continue(job_id, worker_id):
                    cancel_event.set()
                    return
            except Exception:
                logger.exception("Client lease monitor failed for job %s", job_id)

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        daemon=True,
        name=f"heartbeat-{job_id[:8]}",
    )
    heartbeat_thread.start()
    client_monitor_thread = threading.Thread(
        target=client_monitor_loop,
        daemon=True,
        name=f"client-monitor-{job_id[:8]}",
    )
    client_monitor_thread.start()
    _execution_context.cancel_event = cancel_event
    try:
        fn = _resolve_task(task_name)
        parameters = inspect.signature(fn).parameters
        runtime_kwargs = dict(kwargs)
        if "progress_cb" in parameters:
            runtime_kwargs["progress_cb"] = lambda frac, phase: _update_progress(
                job_id, worker_id, frac, phase
            )
        if "cancel_event" in parameters:
            runtime_kwargs["cancel_event"] = cancel_event
        result = fn(*args, **runtime_kwargs)
        if cancel_event.is_set():
            raise JobCancelled("Job cancelled after browser tab closed")
        _finish_job(job_id, worker_id, status="done", result=result)
    except JobCancelled:
        _finish_job(job_id, worker_id, status="cancelled", phase="Cancelled")
    except JobUserError as exc:
        _finish_job(job_id, worker_id, status="error", error=str(exc)[:500])
    except Exception:  # noqa: BLE001 - worker boundary must persist a safe error
        logger.exception("Background job %s failed", job_id)
        _finish_job(
            job_id,
            worker_id,
            status="error",
            error="An internal error occurred. Check the server logs or contact support.",
        )
    finally:
        _execution_context.cancel_event = None
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)
        client_monitor_thread.join(timeout=2)


def prune_expired_jobs() -> int:
    """Soft-delete expired terminal job records; never physically delete."""
    cutoff = _utcnow() - timedelta(seconds=_JOB_TTL_SECONDS)
    db = SessionLocal()
    try:
        count = (
            db.query(BackgroundJob)
            .filter(
                BackgroundJob.status.in_(("done", "error", "cancelled")),
                BackgroundJob.finished_at < cutoff,
                BackgroundJob.is_deleted.is_(False),
            )
            .update(
                {
                    BackgroundJob.is_deleted: True,
                    BackgroundJob.updated_at: _utcnow(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return int(count)
    finally:
        db.close()


def submit_inline_job(fn: Callable, *args, owner_id: int, **kwargs) -> str:
    """Test-only helper exercising durable ownership/actions without a worker."""
    job_id = str(uuid.uuid4())
    worker_id = f"inline-test-{uuid.uuid4()}"
    now = _utcnow()
    db = SessionLocal()
    try:
        db.add(
            BackgroundJob(
                id=job_id,
                owner_id=owner_id,
                task_name="test:inline",
                args_json=dumps_value([]),
                kwargs_json=dumps_value({}),
                status="running",
                progress=0.0,
                phase="Starting...",
                attempts=1,
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=30),
                heartbeat_at=now,
                started_at=now,
                created_at=now,
                updated_at=now,
                is_deleted=False,
            )
        )
        db.commit()
    finally:
        db.close()

    def run_inline() -> None:
        try:
            if "progress_cb" in inspect.signature(fn).parameters:
                result = fn(
                    *args,
                    progress_cb=lambda frac, phase: _update_progress(
                        job_id, worker_id, frac, phase
                    ),
                    **kwargs,
                )
            else:
                result = fn(*args, **kwargs)
            _finish_job(job_id, worker_id, status="done", result=result)
        except JobCancelled:
            _finish_job(job_id, worker_id, status="cancelled", phase="Cancelled")
        except Exception:
            logger.exception("Inline test job %s failed", job_id)
            _finish_job(job_id, worker_id, status="error", error="Inline test job failed")

    _inline_executor.submit(run_inline)
    return job_id
