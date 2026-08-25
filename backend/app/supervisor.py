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
    lock = FileLock(str(DATA_DIR / "suite-supervisor.lock"), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        print("The RDC Accounts Suite supervisor is already running.", flush=True)
        return 2

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


if __name__ == "__main__":
    raise SystemExit(main())
