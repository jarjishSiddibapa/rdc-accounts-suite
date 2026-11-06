"""Send history of the applications that mail by hand: recorded, and never able to break a send."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services import send_history

STARTED = datetime(2026, 11, 6, 5, 0, 0)


class RecordBulkTests(unittest.TestCase):
    def _record(self, report):
        with patch.object(send_history.mail, "record_manual_send") as recorded, \
                patch.object(send_history, "login_email", return_value="me@rdc.in"):
            send_history.record_bulk("ultrafine-payment-reminder", 5, STARTED, report, {"sent_from": "me@gmail.com"})
        args = recorded.call_args.args
        return SimpleNamespace(app_key=args[0], by=args[1], status=args[3], error=args[4], detail=args[7])

    def test_a_batch_where_everything_went_is_sent(self):
        got = self._record([{"status": "sent"}, {"status": "sent"}, {"status": "skipped"}])
        self.assertEqual((got.app_key, got.by, got.status, got.error), ("ultrafine-payment-reminder", "me@rdc.in", "sent", None))
        self.assertEqual((got.detail["sent"], got.detail["failed"], got.detail["skipped"]), (2, 0, 1))
        self.assertEqual(got.detail["sent_from"], "me@gmail.com")

    def test_a_batch_with_some_failures_is_partial_and_names_the_first_problem(self):
        got = self._record([{"status": "sent"}, {"status": "failed", "detail": "Invalid address x"}])
        self.assertEqual(got.status, "partial")
        self.assertIn("1 of 2 mails failed", got.error)
        self.assertIn("Invalid address x", got.error)

    def test_a_batch_where_nothing_went_is_failed(self):
        self.assertEqual(self._record([{"status": "failed", "detail": "smtp"}]).status, "failed")
        self.assertEqual(self._record([{"status": "skipped"}]).status, "failed")


class RecordIsBestEffortTests(unittest.TestCase):
    def test_a_problem_recording_never_raises_into_the_send(self):
        with patch.object(send_history.mail, "record_manual_send", side_effect=RuntimeError("db down")), \
                patch.object(send_history, "login_email", return_value="me@rdc.in"), \
                self.assertLogs(send_history.logger, level="ERROR"):
            send_history.record("unaccounted", 5, STARTED, "sent", to=["a@rdc.in"])

    def test_the_recipients_and_messages_reach_the_log(self):
        with patch.object(send_history.mail, "record_manual_send") as recorded, \
                patch.object(send_history, "login_email", return_value="me@rdc.in"):
            send_history.record("unaccounted", 5, STARTED, "sent", to=["a@rdc.in"], cc=["c@rdc.in"],
                                messages=[{"subject": "S"}], detail={"x": 1})
        args = recorded.call_args.args
        self.assertEqual(args[0], "unaccounted")
        self.assertEqual(args[5], {"to": ["a@rdc.in"], "cc": ["c@rdc.in"]})
        self.assertEqual(args[6], [{"subject": "S"}])
        self.assertEqual(args[7], {"x": 1})


class LogRoutesTests(unittest.TestCase):
    def test_each_application_serves_its_own_history_to_people_with_access(self):
        from test_finished_report_mail import AccessTests
        from app.routers import (
            ultrafine_balance_confirmation, ultrafine_fse_reminder, ultrafine_payment_reminder, unaccounted_txn,
        )
        check = AccessTests()._statuses
        for module, key in (
            (unaccounted_txn, "unaccounted"),
            (ultrafine_balance_confirmation, "ultrafine-balance-confirmation"),
            (ultrafine_payment_reminder, "ultrafine-payment-reminder"),
            (ultrafine_fse_reminder, "ultrafine-fse-reminder"),
        ):
            check(module, key, (("GET", "/mail/log"),))


if __name__ == "__main__":
    unittest.main()
