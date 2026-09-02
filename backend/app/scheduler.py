"""Background thread that fires the scheduled MySQL backup once a day, at
the admin-configured time, and periodically sweeps stale scratch uploads.

Deliberately a plain daemon thread polling every ~45s (no
APScheduler/Celery), matching the rest of this app's minimal-server-load
design.
"""

import logging
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from filelock import FileLock, Timeout

from app import audit_middleware, backup
from app import jobs
from app.config import DATA_DIR, SCRATCH_DIR
from app.database import SessionLocal
from app.models import BackupSettings, RateLimitBucket, TrialBalanceUploadToken
from app.regional import now_ist
from app.services.iocl_balance import monitor as iocl_monitor
from app.services.invoice_booking_tracker import monitor as invoice_booking_monitor

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

_POLL_SECONDS = 45

# Every router that saves an upload/output under SCRATCH_DIR is supposed to
# delete it once its job finishes and the result has been downloaded, but in
# practice several paths don't (e.g. a user who never clicks Download, or a
# job that errors before its own cleanup runs) - on a server that stays up
# for weeks, that silently accumulates multi-hundred-MB ERP exports forever.
# This is a generous backstop, not the primary cleanup mechanism: anything
# older than the admin-configured BackupSettings.scratch_cleanup_minutes has
# certainly finished (or died) one way or another.
#
# Kept well below the minimum allowed scratch_cleanup_minutes (5) so a short
# retention setting (e.g. 30 min, to match the app's own session length)
# is actually honored close to on time, rather than the sweep's own polling
# cadence silently doubling how long files really stick around.
_SCRATCH_SWEEP_INTERVAL_SECONDS = 5 * 60  # sweep every 5 min
_last_scratch_sweep = 0.0

# unaccounted_txn's combined "generate all 3 reports, preview, then an
# explicit separate confirm-send click" mail workflow (and the same
# preview-then-confirm-send shape in ultrafine_balance_confirmation and
# ultrafine_payment_reminder) writes its attachments into a dedicated
# SCRATCH_DIR / "<prefix>-<uuid>" directory and keeps them there until the
# user actually confirms - there is no time limit on how long someone can
# sit on that preview before sending it. Reaping that directory on the same
# admin-configured scratch_cleanup_minutes as a simple one-shot report
# download (default 30 min) let the sweep delete pending, not-yet-sent
# attachments out from under a real send, which is what caused an email
# with fake/placeholder-looking data and empty attachments to go out - the
# mail body itself is a separate, already-fixed bug, but this half of the
# incident is a bare deletion race. Give every such workflow its own, much
# longer grace period, independent of the general-purpose setting.
_MAIL_PREVIEW_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours
_MAIL_PREVIEW_DIR_PREFIXES = ("mail-", "balance-confirm-", "payment-reminder-")


def _sweep_scratch() -> None:
    global _last_scratch_sweep
    now = time.time()
    if now - _last_scratch_sweep < _SCRATCH_SWEEP_INTERVAL_SECONDS:
        return
    _last_scratch_sweep = now

    db = SessionLocal()
    try:
        max_age_seconds = _get_or_create_settings(db).scratch_cleanup_minutes * 60
    finally:
        db.close()

    removed = 0
    for path in SCRATCH_DIR.iterdir():
        try:
            entry_max_age = (
                _MAIL_PREVIEW_MAX_AGE_SECONDS
                if path.is_dir() and path.name.startswith(_MAIL_PREVIEW_DIR_PREFIXES)
                else max_age_seconds
            )
            if path.is_file() and now - path.stat().st_mtime > entry_max_age:
                path.unlink()
                removed += 1
            elif path.is_dir() and now - path.stat().st_mtime > entry_max_age:
                # Some tools (e.g. unaccounted_txn's combined mail workflow)
                # write their output into a whole per-request subdirectory
                # rather than a single file - those are otherwise never
                # reclaimed since they're not a "file" this sweep would
                # otherwise touch.
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue  # another process may be writing/reading it right now
    if removed:
        audit_middleware.log_event("scratch.sweep", details={"files_removed": removed})

# Tracks the last calendar date (YYYY-MM-DD, IST) a scheduled
# backup actually ran, so it fires exactly once even though the loop polls
# several times within the minute that matches backup_time.
_scheduler_thread: threading.Thread | None = None
_start_lock = threading.Lock()
_run_lock = FileLock(str(DATA_DIR / "backup-scheduler.lock"), timeout=0)
_last_run_path = DATA_DIR / "backup-last-run.txt"


def _get_or_create_settings(db) -> BackupSettings:
    settings = db.query(BackupSettings).filter(BackupSettings.id == 1).first()
    if settings is None:
        settings = BackupSettings(id=1, enabled=True, backup_time="03:00", max_backups=30)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    elif settings.is_deleted:
        settings.is_deleted = False
        db.commit()
    return settings


def _tick() -> None:
    db = SessionLocal()
    try:
        settings = _get_or_create_settings(db)
        if not settings.enabled:
            return

        now = now_ist()
        today = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        if settings.backup_time != current_time:
            return
        max_backups = settings.max_backups
    finally:
        db.close()

    try:
        with _run_lock:
            if _last_run_path.exists() and _last_run_path.read_text(encoding="utf-8").strip() == today:
                return
            _last_run_path.write_text(today, encoding="utf-8")
            ok, message = backup.run_backup_and_prune(max_backups)
            audit_middleware.log_event("backup.run", details={"ok": ok, "message": message})
    except Timeout:
        return


def _sweep_database_runtime_state() -> None:
    """Soft-delete expired runtime metadata; never hard-delete a DB row."""
    now = _utcnow()
    db = SessionLocal()
    try:
        db.query(RateLimitBucket).filter(
            RateLimitBucket.updated_at < now - timedelta(days=1),
            RateLimitBucket.is_deleted.is_(False),
        ).update(
            {RateLimitBucket.is_deleted: True},
            synchronize_session=False,
        )
        expired_tokens = db.query(TrialBalanceUploadToken).filter(
            TrialBalanceUploadToken.expires_at < now,
            TrialBalanceUploadToken.is_deleted.is_(False),
        ).all()
        for token in expired_tokens:
            token.is_deleted = True
            Path(token.input_path).unlink(missing_ok=True)
            Path(token.parsed_path).unlink(missing_ok=True)
        db.commit()
    finally:
        db.close()
    jobs.prune_expired_jobs()


def _loop() -> None:
    while True:
        try:
            _tick()
        except Exception:  # noqa: BLE001 - one bad tick must not kill the thread
            logger.exception("[scheduler] backup tick failed")
        try:
            _sweep_scratch()
        except Exception:  # noqa: BLE001 - one bad tick must not kill the thread
            logger.exception("[scheduler] scratch sweep failed")
        try:
            _sweep_database_runtime_state()
        except Exception:  # noqa: BLE001 - one bad sweep must not stop scheduling
            logger.exception("[scheduler] runtime-state sweep failed")
        try:
            iocl_monitor.enqueue_due_check()
        except Exception:  # noqa: BLE001 - portal scheduling must not stop other duties
            logger.exception("[scheduler] IOCL balance scheduling failed")
        try:
            invoice_booking_monitor.enqueue_due_check()
        except Exception:  # noqa: BLE001 - tracker scheduling must not stop other duties
            logger.exception("[scheduler] invoice booking tracker scheduling failed")
        time.sleep(_POLL_SECONDS)


def run_forever() -> None:
    """Run the scheduler in its one dedicated supervised process."""
    _loop()


def start_scheduler() -> None:
    global _scheduler_thread
    with _start_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return
        _scheduler_thread = threading.Thread(
            target=_loop,
            daemon=True,
            name="backup-scheduler",
        )
        _scheduler_thread.start()
