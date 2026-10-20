"""Convert a finished .xlsx report to .xlsb (Excel Binary Workbook), safely.

.xlsb holds exactly the same sheets, values, formulas and formats in a much
smaller file, but only Excel itself can write it. Excel automation proved
unreliable on the server (it could hang), so it is used here only as a last
resort for an email attachment that is still too big, and only like this:

- Excel runs in a separate process (xlsb_convert_worker.py) with its own
  Excel instance, never the user's.
- There is a hard time limit. When it passes, the worker and that Excel are
  killed, and the caller falls back to something that needs no Excel.
- Any failure (no Excel, no pywin32, a crash, an empty file) just returns
  False; nothing is raised and no half-written file is left behind.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

CONVERT_TIMEOUT_SECONDS = 600
_WORKER = Path(__file__).with_name("xlsb_convert_worker.py")


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)


def convert_to_xlsb(source: str | Path, destination: str | Path, timeout: int | None = None) -> bool:
    """Write ``source`` (.xlsx) as ``destination`` (.xlsb). True only if a
    non-empty file was produced in time."""
    if os.name != "nt":
        return False
    source, destination = Path(source).resolve(), Path(destination).resolve()
    pid_file = destination.with_suffix(".excelpid")
    destination.unlink(missing_ok=True)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", str(_WORKER), str(source), str(destination), str(pid_file)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            _, err = proc.communicate(timeout=timeout or CONVERT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            if pid_file.exists():
                try:
                    _kill_tree(int(pid_file.read_text().strip()))
                except ValueError:
                    pass
            proc.communicate()
            logger.warning("xlsb conversion timed out; Excel was stopped")
            destination.unlink(missing_ok=True)
            return False
        if proc.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
            logger.warning("xlsb conversion failed: %s", (err or b"").decode(errors="replace")[-500:])
            destination.unlink(missing_ok=True)
            return False
        return True
    except Exception:
        logger.exception("xlsb conversion could not run")
        destination.unlink(missing_ok=True)
        return False
    finally:
        pid_file.unlink(missing_ok=True)
