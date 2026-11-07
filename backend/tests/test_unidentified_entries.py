"""Unidentified Entries: the Unapplied Receipts Register's "***** Unidentified" receipts as an
application of their own - same list as the Unapplied report's Unidentified Customers sheet,
mailed from the system email on the shared automatic-mail options."""

import asyncio
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from openpyxl import load_workbook

from app import jobs, permissions
from app.services import scheduled_report_mail as mail
from app.services.unapplied_receipts import processor
from app.services.unidentified_entries import auto_mail

KEY = "unidentified-entries"


class _Log:
    def __init__(self):
        self.messages = []

    def put(self, item):
        self.messages.append(item)


def _register():
    return pd.DataFrame({
        "Customer Name": ["A Ltd", "***** Unidentified", "***** Unidentified", "B Ltd"],
        "Customer Number": ["1", "", "", "2"],
        "Payment Method": ["HDFC", "Bandhan", "Bandhan", "HDFC"],
        "Payment Number": ["ON/1", "1048/HO", "1049/HO", "ON/2"],
        "Payment Date": ["09-OCT-26", "26-DEC-25", "27-DEC-25", "12-JAN-26"],
        "On Account/ Prepayment Amount": [0.0, 0.0, 0.0, 5970.4],
        "Unapplied Amount": [40000.0, 239359.0, 100.0, 0.0],
    })


def _comments(receipts, log_q, oracle_cfg, cancel_event=None):
    return {"1048/HO": "NEFT FROM SOME PARTY", "1049/HO": ""}


class RegistrationTests(unittest.TestCase):
    def test_it_is_an_application_with_the_shared_automatic_mail(self):
        self.assertIn(KEY, permissions.APP_KEYS)
        self.assertEqual(permissions.APP_LABELS[KEY], "Unidentified Entries")
        self.assertIn(KEY, permissions.REPORT_RECIPIENT_APP_KEYS)
        spec = mail.SPECS[KEY]
        self.assertTrue(spec.scheduled)
        self.assertTrue(spec.allow_hourly)           # the every-few-hours schedule, like the others
        self.assertTrue(spec.fixed_sender)           # always the system email
        mail.validate_template(spec.default_subject, spec.placeholders)
        mail.validate_template(spec.default_intro, spec.placeholders)
        self.assertIs(mail._job_for(KEY), auto_mail.run_auto_mail_job)

    def test_its_jobs_are_allowed_and_share_the_erp_allowance(self):
        for task in ("run_auto_mail_job", "run_preview_job"):
            name = f"app.services.unidentified_entries.auto_mail:{task}"
            self.assertIn(name, jobs._ALLOWED_TASKS)
            self.assertEqual(jobs._RESOURCE_BY_TASK[name], "oracle-gst")

    def test_a_regular_user_can_use_it_when_granted(self):
        granted = SimpleNamespace(role="user", allowed_apps='["%s"]' % KEY)
        self.assertTrue(permissions.user_has_app_access(granted, KEY))
        self.assertFalse(permissions.user_has_app_access(SimpleNamespace(role="user", allowed_apps="[]"), KEY))

    def test_its_routes_follow_its_permission(self):
        from fastapi import FastAPI
        from app import auth
        from app.routers import unidentified_entries as routes
        app = FastAPI()
        app.include_router(routes.router)
        base = f"/api/tools/{KEY}"
        user = SimpleNamespace(role="user", allowed_apps='["gst-invoice-adder"]')      # not granted
        app.dependency_overrides[auth.get_current_user] = lambda: user

        def status(method, path):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}

            async def send(message):
                sent.append(message)

            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                     "path": base + path, "raw_path": (base + path).encode(), "query_string": b"",
                     "headers": [(b"content-type", b"application/json")], "server": ("test", 80),
                     "client": ("test", 1), "scheme": "http", "root_path": ""}
            try:
                asyncio.run(app(scope, receive, send))
            except Exception:
                return None
            return next(m["status"] for m in sent if m["type"] == "http.response.start")

        calls = (("GET", "/auto-mail"), ("PUT", "/auto-mail"), ("POST", "/auto-mail/send-now"),
                 ("GET", "/mail/defaults"), ("POST", "/mail/preview"))
        for method, path in calls:
            self.assertEqual(status(method, path), 403, (method, path))
        user.allowed_apps = '["%s"]' % KEY                                              # granted: not refused
        for method, path in calls:
            self.assertNotEqual(status(method, path), 403, (method, path))


class SplitTests(unittest.TestCase):
    def test_the_unidentified_receipts_are_set_apart_with_their_bank_descriptions(self):
        with patch.object(processor, "_fetch_comments", _comments):
            rest, unidentified = processor.split_unidentified(_register(), _Log(), None)
        self.assertEqual(rest["Customer Name"].tolist(), ["A Ltd", "B Ltd"])
        self.assertEqual(unidentified["Payment Number"].tolist(), ["1048/HO", "1049/HO"])
        self.assertEqual(unidentified["Bank Description"].tolist(), ["NEFT FROM SOME PARTY", ""])

    def test_a_failed_comments_lookup_leaves_the_descriptions_blank_not_the_report_undone(self):
        def boom(*args, **kwargs):
            raise RuntimeError("ERP down")
        log = _Log()
        with patch.object(processor, "_fetch_comments", boom):
            _, unidentified = processor.split_unidentified(_register(), log, None)
        self.assertEqual(unidentified["Bank Description"].tolist(), ["", ""])
        self.assertTrue(any(level == "warn" for level, _ in log.messages))


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, df, look):
        path = str(self.dir / f"u_{look}.xlsx")
        processor.write_unidentified_workbook(df, path, dt.date(2026, 11, 3), look=look)
        return load_workbook(path)

    def unidentified(self):
        with patch.object(processor, "_fetch_comments", _comments):
            return processor.split_unidentified(_register(), _Log(), None)[1]

    def test_the_sheet_is_the_unapplied_reports_unidentified_customers_sheet_without_a_title_bar(self):
        wb = self.write(self.unidentified(), "accounts")
        self.assertEqual(wb.sheetnames, ["Unidentified Customers"])
        ws = wb.active
        headers = [c.value for c in ws[1]]
        self.assertEqual(headers[0], "Customer Name")
        self.assertIn("Bank Description", headers)
        self.assertEqual(ws.cell(row=2, column=headers.index("Payment Number") + 1).value, "1048/HO")
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertFalse(ws.merged_cells.ranges)
        self.assertEqual(ws.cell(row=ws.max_row, column=1).value, "Grand Total")

    def test_no_entries_is_a_sheet_saying_so(self):
        wb = self.write(self.unidentified().iloc[0:0], "accounts")
        self.assertEqual(wb.active["A1"].value, "No unidentified customer entries found.")

    def test_the_default_look_keeps_the_title_bar_like_the_unapplied_report(self):
        wb = self.write(self.unidentified(), "default")
        self.assertIn("As On 03-Nov-2026", wb.active["A1"].value)


class BuildTests(unittest.TestCase):
    def test_the_mail_carries_one_workbook_of_the_entries(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(auto_mail, "SCRATCH_DIR", Path(tmp)), \
                patch.object(auto_mail, "oracle_config", lambda: None), \
                patch.object(auto_mail, "fetch_register_from_erp", lambda cfg, log, cancel: _register()), \
                patch.object(processor, "_fetch_comments", _comments), \
                patch.object(auto_mail, "run_cpu_phase", lambda fn, *args, **kw: fn(*args, **kw)):
            payload = auto_mail.build({}, None, None)
            self.assertEqual(payload.values["rows"], "2")
            self.assertEqual(len(payload.attachment_groups), 1)
            (path,) = payload.attachment_groups[0]
            self.assertTrue(Path(path).name.startswith("Unidentified_Entries_As_On_"))
            wb = load_workbook(path)
            self.assertEqual(wb.active.cell(row=1, column=1).value, "Customer Name")
            self.assertEqual(payload.detail["rows"], 2)
            self.assertEqual(payload.downloads[0][1], path)
            self.assertTrue(Path(path).is_file())


class SplitWhenTooBigTests(unittest.TestCase):
    def test_entries_too_big_for_one_email_even_as_xlsb_are_split_by_rows_with_none_lost(self):
        from app.services import attachment_fit, scheduled_report_mail as mail
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(auto_mail, "SCRATCH_DIR", Path(tmp)), \
                patch.object(auto_mail, "oracle_config", lambda: None), \
                patch.object(auto_mail, "fetch_register_from_erp", lambda cfg, log, cancel: _register()), \
                patch.object(processor, "_fetch_comments", _comments), \
                patch.object(auto_mail, "run_cpu_phase", lambda fn, *args, **kw: fn(*args, **kw)):
            whole = auto_mail.build({}, None, None)
            whole_size = Path(whole.attachment_groups[0][0]).stat().st_size
            self.assertEqual(len(whole.attachment_groups), 1)
            # a limit just under the whole workbook: one row per part now fits, the whole does not
            with patch.object(mail, "MAX_ATTACHMENT_BYTES", whole_size - 1), \
                    patch.object(attachment_fit.xlsx_compact, "compact_xlsx_in_place"), \
                    patch.object(attachment_fit.xlsb_convert, "convert_to_xlsb", lambda s, d: False):
                payload = auto_mail.build({}, None, None)
            files = [Path(p) for group in payload.attachment_groups for p in group]
            self.assertEqual(len(files), 2)
            self.assertTrue(all(f.stat().st_size <= whole_size - 1 for f in files))
            rows = []
            for f in sorted(files):
                ws = load_workbook(f).active
                self.assertEqual(ws.cell(row=1, column=1).value, "Customer Name")      # each part is complete
                rows += [ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)
                         if ws.cell(row=r, column=1).value != "Grand Total"]
            whole_ws = load_workbook(whole.attachment_groups[0][0]).active
            expected = [whole_ws.cell(row=r, column=2).value for r in range(2, whole_ws.max_row + 1)
                        if whole_ws.cell(row=r, column=1).value != "Grand Total"]
            self.assertEqual(sorted(map(str, rows)), sorted(map(str, expected)))     # every entry, exactly once
            self.assertEqual(payload.values["rows"], "2")
            self.assertEqual(len(payload.downloads), 2)
            self.assertIn("split", payload.detail["attachment"])


if __name__ == "__main__":
    unittest.main()
