"""The scheduler's periodic 'alive' line: what the job queue holds, logged without ever stopping the loop."""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import scheduler


NOW = datetime(2026, 11, 6, 4, 0, 0)


def _job(task, **extra):
    now = NOW
    values = {
        "task_name": task, "phase": "Queued", "created_at": now - timedelta(seconds=90),
        "started_at": now - timedelta(seconds=30), "lease_owner": "HOST:1:worker-1:ab",
        "lease_expires_at": now + timedelta(seconds=120),
    }
    values.update(extra)
    return SimpleNamespace(**values)


class QueueSummaryTests(unittest.TestCase):
    def _summary(self, queued, running):
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.side_effect = [
            queued, running,
        ]
        with patch.object(scheduler, "SessionLocal", return_value=db), \
                patch.object(scheduler, "_utcnow", return_value=NOW):
            text = scheduler._queue_summary()
        db.close.assert_called_once()
        return text

    def test_an_empty_queue_is_reported_as_such(self):
        self.assertEqual(self._summary([], []), "0 queued, 0 running")

    def test_waiting_and_running_jobs_are_named_with_how_long(self):
        text = self._summary(
            [_job("app.services.iocl_balance.monitor.run_check_job", phase="Waiting for portal capacity")],
            [_job("app.services.scheduled_report_mail.run_mail_job")],
        )
        self.assertIn("1 queued, 1 running", text)
        self.assertIn("queued run_check_job waiting 90s (Waiting for portal capacity)", text)
        self.assertIn("running run_mail_job on HOST:1:worker-1:ab for 30s (lease 120s)", text)

    def test_a_job_without_a_lease_or_start_time_does_not_break_the_line(self):
        text = self._summary([], [_job("x.y.job", lease_expires_at=None, started_at=None)])
        self.assertIn("running job on", text)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, scheduler, "_last_heartbeat", scheduler._last_heartbeat)

    def test_it_logs_at_most_once_per_interval(self):
        scheduler._last_heartbeat = float("-inf")
        with patch.object(scheduler, "_queue_summary", return_value="0 queued, 0 running"), \
                self.assertLogs(scheduler.logger, level="INFO") as logged:
            scheduler._log_heartbeat()
            scheduler._log_heartbeat()
        self.assertEqual(len(logged.records), 1)
        self.assertIn("[scheduler] alive; jobs: 0 queued, 0 running", logged.output[0])


if __name__ == "__main__":
    unittest.main()
