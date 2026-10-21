"""A mail batch interrupted by a crash must never be run again from the top (recipients
already reached would get a second copy); an ordinary report job is still re-queued."""

import unittest
import uuid
from datetime import UTC, datetime, timedelta

from app import jobs
from app.database import SessionLocal
from app.models import BackgroundJob


def _expired_running_job(task_name: str) -> BackgroundJob:
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    return BackgroundJob(
        id=str(uuid.uuid4()),
        owner_id=820_001,
        task_name=task_name,
        args_json="[]",
        kwargs_json="{}",
        cancel_on_disconnect=False,
        status="running",
        progress=0.5,
        phase="Sending",
        cancel_requested=False,
        priority=-100,
        attempts=1,
        lease_owner="crashed-worker",
        lease_expires_at=now - timedelta(minutes=5),
        heartbeat_at=now - timedelta(minutes=7),
        created_at=now - timedelta(minutes=8),
        started_at=now - timedelta(minutes=8),
        updated_at=now - timedelta(minutes=7),
        is_deleted=False,
    )


class CrashRecoveryDoesNotResendMailTests(unittest.TestCase):
    def test_send_tasks_are_registered_jobs(self):
        self.assertTrue(jobs._NO_RERUN_AFTER_CRASH <= jobs._ALLOWED_TASKS)

    def test_interrupted_send_is_failed_not_requeued_but_a_report_is_requeued(self):
        send = _expired_running_job(sorted(jobs._NO_RERUN_AFTER_CRASH)[0])
        report = _expired_running_job("app.routers.rdc_payables:_run_process_job")
        db = SessionLocal()
        try:
            db.add_all([send, report])
            db.flush()
            jobs._recover_stale_jobs(db, datetime.now(UTC).replace(tzinfo=None))
            self.assertEqual(send.status, "error")
            self.assertIn("not repeated", send.error)
            self.assertIsNotNone(send.finished_at)
            self.assertEqual(report.status, "queued")
        finally:
            db.rollback()  # nothing from this test is ever committed
            db.close()


if __name__ == "__main__":
    unittest.main()
