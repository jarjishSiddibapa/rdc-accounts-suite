"""Automated full MySQL backups via mysqldump - plain subprocess, no external
backup service, matching this app's "minimal server load" design (the same
reason there's no Celery/Redis anywhere else in the suite)."""

import glob
import os
import subprocess
from datetime import datetime
from pathlib import Path

from app.config import (
    DATA_DIR,
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
)
from app.regional import IST, now_ist

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# A local MySQL service (e.g. "MySQL95") is commonly installed on Windows
# without ever being added to PATH, so check its default install location
# first; fall back to relying on PATH if nothing is found there.
_WINDOWS_MYSQLDUMP_GLOB = r"C:\Program Files\MySQL\MySQL Server *\bin\mysqldump.exe"

_BACKUP_TIMEOUT_SECONDS = 60 * 30  # generous upper bound for a full dump


def _find_mysqldump() -> str:
    candidates = sorted(glob.glob(_WINDOWS_MYSQLDUMP_GLOB), reverse=True)
    if candidates:
        return candidates[0]
    return "mysqldump"


def run_backup() -> tuple[bool, str]:
    """Runs mysqldump to produce a full schema+data backup of the suite's
    database. Returns (True, backup_file_path) on success, or
    (False, error_message) on failure (non-zero exit code or exception).
    Never raises."""
    mysqldump_path = _find_mysqldump()
    backup_file = BACKUP_DIR / f"backup_{now_ist():%Y%m%d_%H%M%S}.sql"

    cmd = [
        mysqldump_path,
        "-h", MYSQL_HOST,
        "-P", str(MYSQL_PORT),
        "-u", MYSQL_USER,
        # A complete, single-file, import-anywhere backup: table structure +
        # data (mysqldump's default) plus routines/triggers/events (NOT
        # included by default) so nothing about the schema is left out even
        # if a future table adds one. --single-transaction takes a
        # consistent InnoDB snapshot without locking tables while the app
        # keeps serving live traffic during the dump.
        "--routines",
        "--triggers",
        "--events",
        "--single-transaction",
        # Without this, mysqldump emits "SET @@GLOBAL.GTID_PURGED=..." when
        # the source server has GTID mode on - that statement only succeeds
        # if the destination server's own GTID_EXECUTED history happens to
        # line up exactly, which it essentially never does on a different
        # machine. This is meant to be a portable "restore anywhere" backup,
        # not a replication snapshot, so GTID state is deliberately excluded.
        "--set-gtid-purged=OFF",
        # "--databases NAME" (plural flag, one name) instead of a bare
        # positional NAME: the latter assumes whoever runs the import has
        # already selected/created the target database themselves, so the
        # file has no CREATE DATABASE/USE statement in it at all - which
        # fails ("No database selected") the moment a GUI tool (e.g. MySQL
        # Workbench's "Import from Self-Contained File") just runs the raw
        # script with nothing pre-selected. --databases embeds
        # "CREATE DATABASE IF NOT EXISTS" + "USE" at the top, so the file is
        # genuinely self-contained no matter how it's run.
        "--databases", MYSQL_DATABASE,
    ]

    # Password goes through the environment (MYSQL_PWD), never as a -p
    # command-line argument - a CLI arg would leak the plaintext password to
    # anyone who can see the process list (tasklist/ps) while the dump runs.
    env = dict(os.environ)
    env["MYSQL_PWD"] = MYSQL_PASSWORD

    try:
        with open(backup_file, "wb") as out:
            result = subprocess.run(
                cmd,
                stdout=out,
                stderr=subprocess.PIPE,
                env=env,
                timeout=_BACKUP_TIMEOUT_SECONDS,
            )

        if result.returncode != 0:
            error_detail = result.stderr.decode("utf-8", errors="replace").strip()
            backup_file.unlink(missing_ok=True)
            return False, f"mysqldump exited with code {result.returncode}: {error_detail}"

        return True, str(backup_file)
    except Exception as exc:  # noqa: BLE001 - backups must never raise into the caller
        backup_file.unlink(missing_ok=True)
        return False, str(exc)


def _prune_old_backups(max_backups: int) -> None:
    files = sorted(BACKUP_DIR.glob("*.sql"), key=lambda p: p.stat().st_mtime)
    excess = len(files) - max_backups
    if excess <= 0:
        return
    for f in files[:excess]:
        try:
            f.unlink()
        except OSError:
            pass


def run_backup_and_prune(max_backups: int) -> tuple[bool, str]:
    ok, message = run_backup()
    if ok:
        _prune_old_backups(max_backups)
    return ok, message


def list_backups() -> list[dict]:
    files = sorted(BACKUP_DIR.glob("*.sql"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=IST).isoformat(),
        }
        for f in files
    ]
