"""Bounded child process used by durable queue integration tests."""

import argparse
import os
import time
import uuid

os.environ.setdefault("APP_PROCESS_ROLE", "worker")

from app import jobs  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import BackgroundJob  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", action="append", required=True)
    args = parser.parse_args()
    worker_id = f"integration-probe:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        db = SessionLocal()
        try:
            statuses = dict(
                db.query(BackgroundJob.id, BackgroundJob.status)
                .filter(BackgroundJob.id.in_(args.job_id))
                .all()
            )
        finally:
            db.close()
        if len(statuses) == len(args.job_id) and all(
            status in {"done", "error", "cancelled"} for status in statuses.values()
        ):
            return 0
        job_id = jobs.claim_next_job(worker_id)
        if job_id is None:
            time.sleep(0.1)
            continue
        jobs.execute_job(job_id, worker_id)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
