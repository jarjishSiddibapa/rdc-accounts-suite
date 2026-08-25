"""Dedicated durable-job worker process."""

import argparse
import logging
import os
import signal
import socket
import threading
import time
import uuid

os.environ.setdefault("APP_PROCESS_ROLE", "worker")

from app import config, database, jobs  # noqa: E402
from app.runtime_logging import configure_runtime_logging  # noqa: E402

logger = logging.getLogger(__name__)


def run(worker_name: str) -> None:
    configure_runtime_logging(worker_name)
    database.init_db()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{worker_name}:{uuid.uuid4().hex[:8]}"
    stopping = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stopping.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), request_stop)

    logger.info("Job worker %s started", worker_id)
    while not stopping.is_set():
        try:
            job_id = jobs.claim_next_job(worker_id)
            if job_id is None:
                stopping.wait(config.JOB_POLL_SECONDS)
                continue
            logger.info("Claimed job %s", job_id)
            jobs.execute_job(job_id, worker_id)
        except Exception:  # noqa: BLE001 - supervisor should not lose a worker to one poll
            logger.exception("Worker loop failed")
            stopping.wait(min(5.0, config.JOB_POLL_SECONDS * 4))
    logger.info("Job worker %s stopped", worker_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="worker")
    args = parser.parse_args()
    run(args.name)


if __name__ == "__main__":
    main()
