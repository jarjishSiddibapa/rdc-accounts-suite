"""Email a finished report by hand (routers/finished_report_mail.py), shared by the report applications."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import finished_report_mail as frm

USER = SimpleNamespace(id=7, email="me@rdc.in")
SENDER = {"configured": True, "email": "me@rdc.in", "app_password": "pw", "signature": "Regards\nMe"}


def _endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


class HelperTests(unittest.TestCase):
    def test_the_recipient_sees_the_download_name_not_the_scratch_file_name(self):
        path = Path("/scratch/3f2a9c_output.xlsx")
        self.assertEqual(frm.attachment_name({"download_filename": "Trial Balance Aug.xlsx"}, path, "Report"), "Trial Balance Aug.xlsx")
        self.assertEqual(frm.attachment_name({"download_filename": "Trial Balance"}, path, "Report"), "Trial Balance.xlsx")
        self.assertEqual(frm.attachment_name({"original_filename": "ledger.xls"}, path, "Report"), "ledger.xls.xlsx")
        self.assertEqual(frm.attachment_name({}, path, "Closing Period Report"), "Closing Period Report.xlsx")

    def test_a_download_name_cannot_carry_a_path_or_unsafe_characters(self):
        path = Path("/scratch/out.xlsx")
        name = frm.attachment_name({"download_filename": "../../etc/pass:wd?.xlsx"}, path, "Report")
        self.assertNotIn("/", name)
        self.assertNotIn(":", name)
        self.assertNotIn("?", name)
        self.assertTrue(name.endswith(".xlsx"))

    def test_the_message_text_is_escaped_and_keeps_its_line_breaks(self):
        body = frm.message_html("Hi <b>team</b>\nBye", "Regards\nMe")
        self.assertIn("Hi &lt;b&gt;team&lt;/b&gt;<br>", body)
        self.assertNotIn("<b>team</b>", body)
        self.assertIn("Regards<br>Me", body)


class SendTests(unittest.TestCase):
    def setUp(self):
        self.router = frm.make_finished_report_mail_router("trial-balance", "Trial Balance Location Wise Report")
        self.send = _endpoint(self.router, "/email/send")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = Path(self.tmp.name) / "9f8e7d_report.xlsx"
        self.report.write_bytes(b"workbook bytes")
        scratch = patch.object(frm, "SCRATCH_DIR", Path(self.tmp.name))
        scratch.start()
        self.addCleanup(scratch.stop)
        limiter = patch.object(frm.auth_limiter, "enforce")
        limiter.start()
        self.addCleanup(limiter.stop)

    def _body(self, **overrides):
        values = dict(job_id="job-1", request_id="abcdef12-3456", to=["A@rdc.in"], cc=["c@rdc.in"],
                      subject="Trial balance", message="Please find attached.")
        values.update(overrides)
        return frm.SendBody(**values)

    def _job(self, **overrides):
        job = {"status": "done", "result": {"output_path": str(self.report), "download_filename": "TB.xlsx"}}
        job.update(overrides)
        return job

    def _run(self, body, job=None, claim="claimed", sender=SENDER, send_error=None):
        calls = {}
        with patch.object(frm, "get_job", return_value=self._job() if job is None else job), \
                patch.object(frm.mailer_shared, "get_email_settings", return_value=sender), \
                patch.object(frm, "claim_job_action", return_value=(claim, None)) as claimed, \
                patch.object(frm, "finish_job_action") as finished, \
                patch.object(frm.mailer_shared, "send_mail", side_effect=send_error) as sent, \
                patch.object(frm.mail, "record_manual_send") as recorded, \
                patch.object(frm.mailer_shared, "remember_recipients") as remembered:
            try:
                calls["result"] = self.send(body, USER)
            except HTTPException as exc:
                calls["error"] = exc
        calls.update(claimed=claimed, finished=finished, sent=sent, recorded=recorded, remembered=remembered)
        return SimpleNamespace(**{k: calls.get(k) for k in ("result", "error", "claimed", "finished", "sent", "recorded", "remembered")})

    def test_it_sends_the_finished_report_from_the_persons_own_email(self):
        calls = self._run(self._body())
        self.assertEqual(calls.result["status"], "sent")
        kwargs = calls.sent.call_args.kwargs
        self.assertEqual(kwargs["from_email"], "me@rdc.in")
        self.assertEqual(kwargs["to_addresses"], ["a@rdc.in"])
        self.assertEqual(kwargs["cc_addresses"], ["c@rdc.in"])
        self.assertEqual(kwargs["subject"], "Trial balance")
        self.assertEqual(Path(kwargs["attachments"][0]).name, "TB.xlsx")
        self.assertIn("Regards<br>Me", kwargs["html_body"])
        self.assertEqual(calls.recorded.call_args.args[3], "sent")
        calls.remembered.assert_called_once()
        calls.finished.assert_called_once()
        self.assertTrue(calls.finished.call_args.kwargs["succeeded"])
        # the staging copy (with the friendly name) is removed afterwards; the report itself is untouched
        self.assertFalse(list(Path(self.tmp.name).glob("email-report-*")))
        self.assertTrue(self.report.is_file())

    def test_the_attachment_is_a_copy_of_the_report(self):
        seen = {}

        def capture(**kwargs):
            seen["bytes"] = Path(kwargs["attachments"][0]).read_bytes()

        with patch.object(frm, "get_job", return_value=self._job()), \
                patch.object(frm.mailer_shared, "get_email_settings", return_value=SENDER), \
                patch.object(frm, "claim_job_action", return_value=("claimed", None)), \
                patch.object(frm, "finish_job_action"), \
                patch.object(frm.mailer_shared, "send_mail", side_effect=capture), \
                patch.object(frm.mail, "record_manual_send"), patch.object(frm.mailer_shared, "remember_recipients"):
            self.send(self._body(), USER)
        self.assertEqual(seen["bytes"], b"workbook bytes")

    def test_a_failed_send_is_reported_logged_and_can_be_pressed_again(self):
        calls = self._run(self._body(), send_error=OSError("smtp down"))
        self.assertEqual(calls.error.status_code, 502)
        self.assertIn("smtp down", calls.error.detail)
        self.assertEqual(calls.recorded.call_args.args[3], "failed")
        self.assertFalse(calls.finished.call_args.kwargs["succeeded"])
        calls.remembered.assert_not_called()
        self.assertFalse(list(Path(self.tmp.name).glob("email-report-*")))

    def test_the_same_click_cannot_send_twice(self):
        for state, status in (("completed", 409), ("in_progress", 409), ("missing", 404)):
            calls = self._run(self._body(), claim=state)
            self.assertEqual(calls.error.status_code, status, state)
            calls.sent.assert_not_called()

    def test_nothing_is_sent_when_the_request_is_not_in_order(self):
        cases = {
            "no sender": dict(sender={**SENDER, "configured": False}),
            "bad request id": dict(body=self._body(request_id="x")),
            "overlap": dict(body=self._body(to=["a@rdc.in"], cc=["A@rdc.in"])),
            "multi-line subject": dict(body=self._body(subject="Hi\nBcc: evil@example.com")),
            "not finished": dict(job=self._job(status="running")),
            "file gone": dict(job=self._job(result={"output_path": str(self.report) + ".gone"})),
        }
        for name, case in cases.items():
            calls = self._run(case.get("body", self._body()), job=case.get("job"), sender=case.get("sender", SENDER))
            self.assertIsNotNone(calls.error, name)
            self.assertIn(calls.error.status_code, (400, 404, 409), name)
            calls.sent.assert_not_called()

    def test_a_report_too_large_to_email_is_refused_with_a_clear_message(self):
        with patch.object(frm.mail, "MAX_ATTACHMENT_BYTES", 5):
            calls = self._run(self._body())
        self.assertEqual(calls.error.status_code, 400)
        self.assertIn("too large to email", calls.error.detail)
        calls.sent.assert_not_called()

    def test_a_person_can_only_email_their_own_job(self):
        # get_job is asked for the signed-in person's own jobs only
        calls = self._run(self._body())
        self.assertEqual(calls.result["status"], "sent")
        with patch.object(frm, "get_job", return_value=None) as asked, \
                patch.object(frm.mailer_shared, "get_email_settings", return_value=SENDER):
            with self.assertRaises(HTTPException):
                self.send(self._body(), USER)
        self.assertEqual(asked.call_args.kwargs["owner_id"], USER.id)


class DefaultsTests(unittest.TestCase):
    def test_the_defaults_start_from_the_last_recipients_and_the_report_name(self):
        router = frm.make_finished_report_mail_router("closing-period-report", "Closing Period Report")
        defaults = _endpoint(router, "/email/defaults")
        with patch.object(frm.mailer_shared, "get_prefill_recipients", return_value={"to": ["a@rdc.in"], "cc": []}), \
                patch.object(frm.mailer_shared, "get_email_settings", return_value=SENDER):
            result = defaults(db=object(), user=USER)
        self.assertEqual(result["to"], ["a@rdc.in"])
        self.assertTrue(result["subject"].startswith("Closing Period Report as on "))
        self.assertIn("Closing Period Report", result["message"])
        self.assertTrue(result["sender_configured"])


class AccessTests(unittest.TestCase):
    """The new routes follow the application's own permission: granted people reach them, others get 403."""

    def _statuses(self, module, key, calls):
        import asyncio
        from fastapi import FastAPI
        from app import auth

        app = FastAPI()
        app.include_router(module.router)
        user = SimpleNamespace(role="user", allowed_apps='["%s"]' % key)
        app.dependency_overrides[auth.get_current_user] = lambda: user

        def status(method, path):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}

            async def send(message):
                sent.append(message)

            full = module.router.prefix + path
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                     "path": full, "raw_path": full.encode(), "query_string": b"",
                     "headers": [(b"content-type", b"application/json")], "server": ("test", 80),
                     "client": ("test", 1), "scheme": "http", "root_path": ""}
            try:
                asyncio.run(app(scope, receive, send))
            except Exception:                    # past the permission check, the database is not there
                return None
            return next(m["status"] for m in sent if m["type"] == "http.response.start")

        for method, path in calls:
            self.assertNotEqual(status(method, path), 403, (key, method, path))
        user.allowed_apps = '["some-other-app"]'
        for method, path in calls:
            self.assertEqual(status(method, path), 403, (key, method, path))

    def test_the_report_applications_email_routes_follow_their_permission(self):
        from app.routers import (
            closing_period, creditors_ageing, erp_converter, gstr2b, rdc_payables, trial_balance,
            trial_balance_formatter, unapplied_receipts,
        )
        calls = (("GET", "/email/defaults"), ("POST", "/email/send"), ("GET", "/email/log"))
        for module, key in (
            (closing_period, "closing-period-report"), (creditors_ageing, "creditors-ageing-report"),
            (erp_converter, "erp-to-excel"), (gstr2b, "gstr2b-combinator"), (rdc_payables, "rdc-payables"),
            (trial_balance, "trial-balance"), (trial_balance_formatter, "trial-balance-formatter"),
            (unapplied_receipts, "unapplied-receipts"),
        ):
            self._statuses(module, key, calls)

    def test_the_monitors_send_mail_routes_follow_their_permission(self):
        from app.routers import invoice_booking_tracker, iocl_balance
        calls = (("GET", "/mail/defaults"), ("POST", "/mail/send"))
        self._statuses(iocl_balance, "iocl-balance-monitor", calls)
        self._statuses(invoice_booking_tracker, "invoice-booking-tracker", calls)

    def test_the_applications_can_have_default_recipients_set_by_an_administrator(self):
        from app import permissions
        for key in ("closing-period-report", "creditors-ageing-report", "erp-to-excel", "gstr2b-combinator",
                    "rdc-payables", "trial-balance", "trial-balance-formatter", "unapplied-receipts"):
            self.assertIn(key, permissions.REPORT_RECIPIENT_APP_KEYS)
            self.assertIn(key, permissions.APP_KEYS)


if __name__ == "__main__":
    unittest.main()
