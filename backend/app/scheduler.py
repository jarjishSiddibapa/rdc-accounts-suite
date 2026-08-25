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
from filelock import FileLock, Timeout

from app import audit_middleware, backup
from app.config import DATA_DIR, SCRATCH_DIR
from app.database import SessionLocal
from app.models import BackupSettings
from app.regional import now_ist

logger = logging.getLogger(__name__)

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
            if path.is_file() and now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
                removed += 1
            elif path.is_dir() and now - path.stat().st_mtime > max_age_seconds:
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
        time.sleep(_POLL_SECONDS)


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
