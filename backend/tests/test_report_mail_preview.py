"""Mailing a report by hand: the preview shows exactly what will go out, and
nothing is sent until the person confirms."""

import datetime as dt
import smtplib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openpyxl
from fastapi import HTTPException

from app.routers import report_mail_routes
from app.services import report_mail_preview
from app.services import scheduled_report_mail as mail
from test_scheduled_report_mail import APP, GOOD, _DbCase

SENDER = {"configured": True, "email": "me@gmail.com", "app_password": "pw", "signature": "Regards,\nMe"}
USER = SimpleNamespace(id=7, email="me@rdc.in")


def _payload(tmp, groups=1):
    paths = []
    for index in range(groups):
        path = Path(tmp) / f"Report_{index}.xlsx"
        wb = openpyxl.Workbook()
        wb.active.append(["a", "b"])
        wb.save(path)
        paths.append(str(path))
    return mail.MailPayload(
        values={"date": "19-Oct-2026"},
        sections=[("Untagged Summary", "<table><tr><td>x</td></tr></table>")],
        attachment_groups=[[p] for p in paths],
        downloads=[("Report", paths[0])],
        notes=["a note"], detail={"rows": 3},
    )


class BuildPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, build, sender=SENDER, **kw):
        with patch.object(report_mail_preview, "get_email_settings", lambda uid: sender):
            return report_mail_preview.build_preview(
                APP, build, 7, kw.get("to", ["a@rdc.in"]), kw.get("cc", ["b@rdc.in"]),
                kw.get("subject", "Report as on {date}"), kw.get("intro", "Hello {date}"),
                kw.get("allow_unmapped", False),
            )

    def test_the_preview_is_the_exact_mail_with_the_persons_own_sender_and_signature(self):
        seen = {}

        def build(snapshot, progress_cb, cancel_event):
            seen.update(snapshot)
            return _payload(self.tmp.name)

        result = self._run(build)
        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["from_email"], "me@gmail.com")
        self.assertEqual((result["to"], result["cc"]), (["a@rdc.in"], ["b@rdc.in"]))
        message = result["messages"][0]
        self.assertEqual(message["subject"], "Report as on 19-Oct-2026")
        self.assertIn("Hello 19-Oct-2026", message["html_body"])
        self.assertIn("<table>", message["html_body"])
        self.assertIn("Regards,<br>Me", message["html_body"])         # their own signature, not the auto-mail one
        self.assertEqual(message["attachments"][0]["name"], "Report_0.xlsx")
        self.assertGreater(message["attachments"][0]["bytes"], 0)
        self.assertEqual(result["downloads"][0]["label"], "Report")
        self.assertEqual(result["notes"], ["a note"])
        self.assertEqual(seen["send_when_unmapped"], False)
        self.assertEqual(seen["signature"], "Regards,\nMe")

    def test_a_split_report_previews_every_mail(self):
        result = self._run(lambda *a: _payload(self.tmp.name, groups=2))
        self.assertEqual([m["subject"] for m in result["messages"]],
                         ["Report as on 19-Oct-2026 (Part 1 of 2)", "Report as on 19-Oct-2026 (Part 2 of 2)"])

    def test_missing_mappings_stop_the_preview_unless_allowed(self):
        held = mail.MailPayload(values={}, hold_reason="2 unmapped", detail={"unmapped": [{"category": "X", "items": ["A"]}]})
        result = self._run(lambda *a: held)
        self.assertEqual(result["status"], "needs_mapping_fix")
        self.assertEqual(result["validation_warnings"][0]["items"], ["A"])

    def test_allow_unmapped_is_passed_to_the_builder(self):
        seen = {}

        def build(snapshot, *_):
            seen.update(snapshot)
            return _payload(self.tmp.name)

        self._run(build, allow_unmapped=True)
        self.assertTrue(seen["send_when_unmapped"])

    def test_no_email_sender_set_up_builds_nothing(self):
        build = MagicMock()
        result = self._run(build, sender={"configured": False, "email": "", "app_password": "", "signature": ""})
        self.assertEqual(result["status"], "email_not_configured")
        build.assert_not_called()


class RoutesTests(_DbCase):
    def setUp(self):
        super().setUp()
        self.preview_job = lambda *a: None
        router = report_mail_routes.make_report_mail_router(APP, self.preview_job)
        self.endpoints = {(r.path, tuple(sorted(r.methods))): r.endpoint for r in router.routes}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, value in (
            ("get_email_settings", lambda uid: SENDER),
            ("get_report_recipient_defaults", lambda db, key: {"to": ["a@rdc.in"], "cc": ["b@rdc.in"]}),
        ):
            patcher = patch.object(report_mail_routes.mailer_shared, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ep(self, path, method):
        return self.endpoints[(path, (method,))]

    # defaults / preview request -------------------------------------------------
    def test_defaults_prefill_recipients_and_wording(self):
        result = self.ep("/mail/defaults", "GET")(self.db, USER)
        self.assertEqual((result["to"], result["cc"]), (["a@rdc.in"], ["b@rdc.in"]))
        self.assertIn("{date}", result["subject"])
        self.assertTrue(result["sender_configured"])
        self.assertEqual(result["sender_email"], "me@gmail.com")

    def test_preview_queues_a_job_for_the_person_without_sending(self):
        body = report_mail_routes.PreviewBody(to=["A@rdc.in "], cc=[], subject="S {date}", intro="Hi")
        with patch.object(report_mail_routes, "submit_job", return_value="job-1") as submit:
            result = self.ep("/mail/preview", "POST")(body, USER)
        self.assertEqual(result, {"job_id": "job-1"})
        args = submit.call_args.args
        self.assertEqual(args[0], self.preview_job)
        self.assertEqual(args[1:6], (7, ["a@rdc.in"], [], "S {date}", "Hi"))
        self.assertEqual(submit.call_args.kwargs["owner_id"], 7)

    def test_preview_input_problems_are_a_400_with_the_reason(self):
        cases = [
            ({"to": []}, "To recipient"),
            ({"to": ["nope"]}, "valid email"),
            ({"cc": ["a@rdc.in"]}, "To or Cc"),
            ({"subject": "a\nb"}, "single"),
            ({"intro": "  "}, "cannot be empty"),
            ({"intro": "Hi {unknown}"}, "not available"),
        ]
        for change, expected in cases:
            with self.subTest(change=change):
                base = {"to": ["a@rdc.in"], "cc": [], "subject": "S", "intro": "Hi"}
                body = report_mail_routes.PreviewBody(**{**base, **change})
                with self.assertRaises(HTTPException) as caught, patch.object(report_mail_routes, "submit_job") as submit:
                    self.ep("/mail/preview", "POST")(body, USER)
                self.assertEqual(caught.exception.status_code, 400)
                self.assertIn(expected, caught.exception.detail)
                submit.assert_not_called()

    def test_preview_needs_the_persons_own_email_set_up(self):
        body = report_mail_routes.PreviewBody(to=["a@rdc.in"], subject="S", intro="Hi")
        with patch.object(report_mail_routes.mailer_shared, "get_email_settings", lambda uid: {"configured": False}):
            with self.assertRaises(HTTPException) as caught:
                self.ep("/mail/preview", "POST")(body, USER)
        self.assertIn("Settings", caught.exception.detail)

    # confirm-send ---------------------------------------------------------------
    def _preview_result(self, messages=1):
        files = []
        for i in range(messages):
            path = Path(self.tmp.name) / f"r{i}.xlsx"
            path.write_bytes(b"xlsx")
            files.append(str(path))
        return {
            "status": "preview", "from_email": "me@gmail.com", "to": ["a@rdc.in"], "cc": ["b@rdc.in"],
            "messages": [{"subject": f"S{i}", "html_body": "<p>x</p>", "attachments": [{"path": p, "name": Path(p).name, "bytes": 4}]}
                         for i, p in enumerate(files)],
            "downloads": [{"label": "R", "path": files[0], "name": "r0.xlsx", "bytes": 4}],
            "detail": {"rows": 3},
        }

    def _confirm(self, result, claim="claimed", send=None, status="done"):
        job = {"status": status, "result": result}
        send = send or MagicMock()
        finish = MagicMock()
        with patch.object(report_mail_routes, "get_job", return_value=job), \
                patch.object(report_mail_routes, "claim_job_action", return_value=(claim, None)), \
                patch.object(report_mail_routes, "finish_job_action", finish), \
                patch.object(report_mail_routes.mailer_shared, "send_mail", send):
            try:
                out = self.ep("/mail/confirm-send", "POST")(report_mail_routes.ConfirmBody(job_id="j"), USER)
            except HTTPException as exc:
                return exc, send, finish
        return out, send, finish

    def test_confirm_sends_the_built_mail_from_the_persons_own_sender_and_logs_it(self):
        out, send, finish = self._confirm(self._preview_result())
        self.assertEqual(out["status"], "sent")
        kwargs = send.call_args.kwargs
        self.assertEqual((kwargs["from_email"], kwargs["app_password"]), ("me@gmail.com", "pw"))
        self.assertEqual((kwargs["to_addresses"], kwargs["cc_addresses"]), (["a@rdc.in"], ["b@rdc.in"]))
        self.assertEqual(kwargs["subject"], "S0")
        self.assertEqual(Path(kwargs["attachments"][0]).name, "r0.xlsx")
        self.assertTrue(finish.call_args.kwargs["succeeded"])
        entry = mail.log_view(self.db, APP)["items"][0]
        self.assertEqual((entry["status"], entry["trigger"], entry["requested_by"]), ("sent", "manual", "me@rdc.in"))
        self.assertEqual(entry["messages"][0]["subject"], "S0")
        self.assertEqual(entry["detail"]["sent_from"], "me@gmail.com")

    def test_every_part_of_a_split_report_is_sent(self):
        out, send, _ = self._confirm(self._preview_result(messages=3))
        self.assertEqual([c.kwargs["subject"] for c in send.call_args_list], ["S0", "S1", "S2"])
        self.assertEqual(len(out["messages"]), 3)

    def test_a_failure_part_way_is_reported_logged_and_not_retried_blindly(self):
        send = MagicMock(side_effect=[None, smtplib.SMTPException("boom")])
        out, _, finish = self._confirm(self._preview_result(messages=3), send=send)
        self.assertIsInstance(out, HTTPException)
        self.assertIn("1 of 3", out.detail)
        self.assertFalse(finish.call_args.kwargs["succeeded"])
        entry = mail.log_view(self.db, APP)["items"][0]
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(len(entry["messages"]), 1)           # the part that did go out is on record

    def test_a_bad_sender_password_says_so_plainly(self):
        send = MagicMock(side_effect=smtplib.SMTPAuthenticationError(535, b"no"))
        out, _, _ = self._confirm(self._preview_result(), send=send)
        self.assertIn("app password", out.detail)

    def test_only_a_finished_preview_can_be_confirmed(self):
        for result, status in (({"status": "needs_mapping_fix"}, "done"), (self._preview_result(), "running")):
            out, send, _ = self._confirm(result, status=status)
            self.assertEqual(out.status_code, 400)
            send.assert_not_called()

    def test_a_preview_already_sent_cannot_be_sent_twice(self):
        out, send, _ = self._confirm(self._preview_result(), claim="completed")
        self.assertEqual(out.status_code, 409)
        self.assertIn("already been sent", out.detail)
        send.assert_not_called()

    # download -------------------------------------------------------------------
    def test_the_full_report_can_be_downloaded_from_the_preview(self):
        result = self._preview_result()
        with patch.object(report_mail_routes, "get_job", return_value={"status": "done", "result": result}):
            response = self.ep("/mail/download/{job_id}/{index}", "GET")("j", 0, USER)
            self.assertEqual(Path(response.path).name, "r0.xlsx")
            with self.assertRaises(HTTPException) as caught:
                self.ep("/mail/download/{job_id}/{index}", "GET")("j", 5, USER)
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
