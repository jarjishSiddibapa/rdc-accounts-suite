"""Stop processes left by an earlier ``start_all.bat`` invocation.

The old launcher trusted one PID file, suppressed ``taskkill`` failures, and
immediately started another supervisor.  A stale/mismatched PID or a process
tree that took a moment to exit therefore left the FileLock held and the next
supervisor returned code 2 even though parts of the old suite were still alive.

This module deliberately scopes cleanup to Python executables inside this
repository's own virtual environment.  It never kills an arbitrary process
merely because it uses port 2805.  Supervisor trees are terminated first, then
any orphaned API/worker/scheduler/CPU children using that exact venv are
removed.  Startup proceeds only after the process set and listening port are
both verified clear.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
PID_FILE = DATA_DIR / "supervisor.pid"
STOP_FILE = DATA_DIR / "supervisor.stop-requested"
VENV_PYTHONS = {
    str((BACKEND_DIR / "venv" / "Scripts" / name).resolve()).casefold()
    for name in ("python.exe", "pythonw.exe")
}
SUITE_MARKERS = (
    "app.supervisor",
    "app.worker",
    "app.scheduler_runner",
    "uvicorn",
    "multiprocessing.spawn",
)
@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    parent_pid: int
    executable: str
    command_line: str


class CleanupError(RuntimeError):
    pass


def _powershell_json(script: str):
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise CleanupError(f"Could not inspect Windows processes: {detail}")
    payload = completed.stdout.strip()
    return json.loads(payload) if payload else []


def _list_processes() -> list[ProcessInfo]:
    if os.name != "nt":
        return []
    rows = _powershell_json(
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine) "
        "| ConvertTo-Json -Compress"
    )
    if isinstance(rows, dict):
        rows = [rows]
    result = []
    for row in rows:
        try:
            result.append(
                ProcessInfo(
                    pid=int(row.get("ProcessId") or 0),
                    parent_pid=int(row.get("ParentProcessId") or 0),
                    executable=str(row.get("ExecutablePath") or ""),
                    command_line=str(row.get("CommandLine") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


def _is_project_python(process: ProcessInfo, own_pid: int | None = None) -> bool:
    if own_pid is not None and process.pid == own_pid:
        return False
    try:
        executable = str(Path(process.executable).resolve()).casefold()
    except (OSError, ValueError):
        executable = process.executable.casefold()
    return bool(executable) and executable in VENV_PYTHONS


def _is_supervisor(process: ProcessInfo) -> bool:
    return "app.supervisor" in process.command_line.casefold()


def _suite_processes(processes: list[ProcessInfo], own_pid: int | None = None) -> list[ProcessInfo]:
    """Return this project's known long-running suite processes.

    Exact executable scoping prevents another repository's workers from being
    touched. Command-line scoping also avoids killing a developer's test,
    dependency installation, or maintenance command that happens to use this
    venv while the launcher is preparing a restart.
    """
    return [
        process
        for process in processes
        if _is_project_python(process, own_pid=own_pid)
        and any(marker in process.command_line.casefold() for marker in SUITE_MARKERS)
    ]


def _port_owners(port: int) -> list[int]:
    if os.name != "nt":
        return []
    rows = _powershell_json(
        "$ErrorActionPreference='Stop'; "
        f"@(Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty OwningProcess -Unique) | ConvertTo-Json -Compress"
    )
    if isinstance(rows, int):
        return [rows]
    return [int(value) for value in rows or []]


def _terminate_tree(pid: int) -> None:
    completed = subprocess.run(
        ["taskkill.exe", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode == 0:
        return
    # A process can exit between discovery and taskkill. Only treat it as an
    # error when the PID is still present after the failed command.
    if any(process.pid == pid for process in _list_processes()):
        detail = (completed.stderr or completed.stdout).strip()
        raise CleanupError(f"Could not stop previous suite process PID {pid}: {detail}")


def _read_registered_pid() -> int | None:
    try:
        value = PID_FILE.read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except (OSError, ValueError):
        return None


def _request_graceful_supervisor_stop(
    supervisors: list[ProcessInfo], wait_seconds: float
) -> list[ProcessInfo]:
    """Ask a live supervisor to unwind its children and return normally.

    Returning normally matters to the already-open ``start_all.bat`` window:
    it must not display a false crash merely because another launcher requested
    a clean restart. Any supervisor that ignores the request is returned for a
    checked ``taskkill /T`` fallback.
    """
    if not supervisors:
        return []
    try:
        STOP_FILE.write_text("restart\n", encoding="utf-8")
    except OSError as exc:
        raise CleanupError(f"Could not request a graceful suite restart: {exc}") from exc

    target_pids = {process.pid for process in supervisors}
    deadline = time.monotonic() + max(1.0, wait_seconds)
    while time.monotonic() < deadline:
        remaining = [
            process
            for process in _suite_processes(_list_processes(), own_pid=os.getpid())
            if process.pid in target_pids
        ]
        if not remaining:
            return []
        time.sleep(0.25)
    return [
        process
        for process in _suite_processes(_list_processes(), own_pid=os.getpid())
        if process.pid in target_pids
    ]


def cleanup_previous_suite(timeout_seconds: float = 30.0, port: int = 2805) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    own_pid = os.getpid()
    initial = _suite_processes(_list_processes(), own_pid=own_pid)
    registered_pid = _read_registered_pid()

    # Kill supervisor roots first so taskkill /T also collects uvicorn's own
    # multiprocessing children. Include every discovered supervisor; an older
    # race may have left more than one PID while only the newest reached the file.
    supervisors = [process for process in initial if _is_supervisor(process)]
    if registered_pid is not None:
        registered = next((p for p in initial if p.pid == registered_pid), None)
        if registered is not None and registered not in supervisors:
            supervisors.insert(0, registered)
    graceful_wait = min(12.0, max(1.0, timeout_seconds / 2))
    for process in _request_graceful_supervisor_stop(supervisors, graceful_wait):
        _terminate_tree(process.pid)

    # Any remaining exact-venv process is an orphan from this suite boundary.
    time.sleep(0.2)
    for process in _suite_processes(_list_processes(), own_pid=own_pid):
        _terminate_tree(process.pid)

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while True:
        processes = _list_processes()
        remaining = _suite_processes(processes, own_pid=own_pid)
        owners = _port_owners(port)
        project_pids = {process.pid for process in remaining}
        unrelated_owners = [pid for pid in owners if pid not in project_pids]
        if unrelated_owners:
            raise CleanupError(
                f"Port {port} is occupied by unrelated process PID(s) "
                f"{', '.join(str(pid) for pid in unrelated_owners)}. "
                "It was not stopped for safety."
            )
        if not remaining and not owners:
            break
        if time.monotonic() >= deadline:
            details = ", ".join(str(process.pid) for process in remaining) or "none"
            raise CleanupError(
                f"Previous suite processes did not stop within {timeout_seconds:g} seconds "
                f"(remaining PIDs: {details}; port owners: {owners or 'none'})."
            )
        time.sleep(0.25)

    try:
        STOP_FILE.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)
    except OSError as exc:
        raise CleanupError(f"Could not remove stale supervisor PID file: {exc}") from exc
    return len(initial)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--port", type=int, default=2805)
    args = parser.parse_args(argv)
    try:
        count = cleanup_previous_suite(args.timeout, args.port)
    except CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    if count:
        print(f"Stopped {count} process(es) from the previous suite run.", flush=True)
    else:
        print("No previous suite processes are running.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
