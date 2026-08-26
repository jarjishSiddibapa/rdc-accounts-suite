"""Start, monitor, and restart the API, job workers, and scheduler."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from filelock import FileLock, Timeout

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(BACKEND_DIR / ".env")

# Read by start_all.bat before it starts a new instance, so a previous run
# left behind by an abrupt shutdown (window closed before cleanup finished,
# a crash, a forced kill) can be found and torn down by PID rather than the
# batch file just hoping nothing is left over.
PID_FILE = DATA_DIR / "supervisor.pid"


def _enable_kill_on_close() -> None:
    """Put this process into a Windows Job Object with KILL_ON_JOB_CLOSE, so
    every descendant - the uvicorn API process *and* the extra workers uvicorn
    itself forks under `--workers`, the scheduler, the job workers - is force
    -killed by Windows the instant this process ends, no matter how: closing
    the console window, a crash, an external taskkill, not just a clean
    Ctrl+C. Nested job objects are supported since Windows 8, so this is safe
    even if the console host is already inside some other job.

    Without this, only our direct children are terminated on a clean
    Ctrl+C (see the `finally` block in main()); uvicorn's own forked workers
    are grandchildren we never tracked, so they could survive as orphans
    still holding port 2805 open.
    """
    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        print(
            "Warning: could not create a Windows Job Object; if this window "
            "is closed abnormally, worker processes may be left running.",
            flush=True,
        )
        return

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        print("Warning: could not configure the Windows Job Object.", flush=True)
        return

    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        print("Warning: could not assign this process to the Job Object.", flush=True)
        return

    # Keep the handle referenced for the life of this process - if it were
    # garbage collected the job's last handle would close early and every
    # child would be killed immediately, not on shutdown.
    globals()["_KILL_ON_CLOSE_JOB_HANDLE"] = job


def _count(name: str, default: int) -> int:
    return max(1, int(os.environ.get(name, str(default))))


def _spawn(name: str, command: list[str], role: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["APP_PROCESS_ROLE"] = role
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    print(f"Starting {name}: {' '.join(command)}", flush=True)
    return subprocess.Popen(
        command,
        cwd=BACKEND_DIR,
        env=env,
        creationflags=creationflags,
    )


def main() -> int:
    _enable_kill_on_close()
    lock = FileLock(str(DATA_DIR / "suite-supervisor.lock"), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        print("The RDC Accounts Suite supervisor is already running.", flush=True)
        return 2
    PID_FILE.write_text(str(os.getpid()))

    api_workers = _count("API_WORKERS", 2)
    job_workers = _count("JOB_WORKER_PROCESSES", 2)
    python = sys.executable
    children: dict[str, tuple[list[str], str, subprocess.Popen]] = {}
    try:
        init_env = os.environ.copy()
        init_env["APP_PROCESS_ROLE"] = "initializer"
        subprocess.run(
            [python, "-m", "app.initialize"],
            cwd=BACKEND_DIR,
            env=init_env,
            check=True,
        )

        specs: list[tuple[str, list[str], str]] = [
            (
                "api",
                [
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "2805",
                    "--workers",
                    str(api_workers),
                ],
                "api",
            ),
            ("scheduler", [python, "-m", "app.scheduler_runner"], "scheduler"),
        ]
        specs.extend(
            (
                f"worker-{index}",
                [python, "-m", "app.worker", "--name", f"worker-{index}"],
                "worker",
            )
            for index in range(1, job_workers + 1)
        )
        for name, command, role in specs:
            children[name] = (command, role, _spawn(name, command, role))

        print(
            f"RDC Accounts Suite running with {api_workers} API workers, "
            f"{job_workers} processing workers, and 1 scheduler.",
            flush=True,
        )
        print(
            "Close this window (or press Ctrl+C) to stop the entire suite - "
            "every worker process is torn down with it.",
            flush=True,
        )
        while True:
            time.sleep(2)
            for name, (command, role, process) in list(children.items()):
                exit_code = process.poll()
                if exit_code is None:
                    continue
                print(
                    f"{name} exited with code {exit_code}; restarting in 2 seconds...",
                    flush=True,
                )
                time.sleep(2)
                children[name] = (command, role, _spawn(name, command, role))
    except KeyboardInterrupt:
        print("Stopping RDC Accounts Suite...", flush=True)
        return 0
    finally:
        for _name, (_command, _role, process) in children.items():
            if process.poll() is None:
                process.terminate()
        deadline = time.time() + 10
        for _name, (_command, _role, process) in children.items():
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0.1, deadline - time.time()))
                except subprocess.TimeoutExpired:
                    process.kill()
        lock.release()
        try:
            if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
