"""The date a mail schedule is as on reaches each application's own report: asked of the ERP only for a past date,
and not at all (the report exactly as it always was) for "today"."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import erp_ageing
from app.services.gst_invoice_adder import auto_mail as gst_mail
from app.services.unidentified_entries import auto_mail as unidentified_mail
from app.services.untagged_invoices import auto_mail as untagged_mail

SNAPSHOT = {"send_when_unmapped": True, "subject_template": "s", "intro_template": "i", "signature": ""}
PAST = date(2026, 9, 30)


class _Stop(Exception):
    """Raised by a stand-in for the part of the report that is not under test."""


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def patched(self, module, **names):
        for name, value in names.items():
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class UntaggedTests(_Case):
    def run_build(self, as_on):
        seen = {}

        def fake_build_report(path, **kwargs):
            seen["path"], seen["kwargs"] = path, kwargs
            raise _Stop

        self.patched(untagged_mail, SCRATCH_DIR=self.tmp)
        self.patched(untagged_mail.report_job, build_report=fake_build_report)
        self.patched(untagged_mail.erp_ageing, oracle_config=lambda: "cfg")
        self.patched(untagged_mail.customer_region, RegionFetch=MagicMock())
        with self.assertRaises(_Stop):
            untagged_mail.build({**SNAPSHOT, "as_on": as_on})
        return seen

    def test_today_builds_the_report_exactly_as_it_always_did(self):
        seen = self.run_build(None)
        self.assertEqual(set(seen["kwargs"]), {"allow_unmapped", "progress_cb", "cancel_event"})
        self.assertIn(date.today().isoformat(), seen["path"])

    def test_a_past_date_fetches_the_ageing_and_the_regions_as_on_that_date(self):
        fetch = MagicMock(return_value="frame")
        self.patched(erp_ageing, fetch_ageing_from_erp=fetch)
        seen = self.run_build(PAST)
        kwargs = seen["kwargs"]
        self.assertEqual(kwargs["as_on_date"], PAST)
        self.assertIn("2026-09-30", seen["path"])                           # the file name says the date
        self.assertEqual(kwargs["ageing"](), "frame")
        self.assertEqual(fetch.call_args.kwargs["as_on"], PAST)
        self.assertFalse(fetch.call_args.kwargs["include_buckets"])        # this report works out its own buckets
        self.assertTrue(callable(kwargs["regions"]))


class GstTests(_Case):
    def run_build(self, as_on):
        seen = {}

        def fake_build_report(path, name, **kwargs):
            seen["name"], seen["kwargs"] = name, kwargs
            raise _Stop

        self.patched(gst_mail, SCRATCH_DIR=self.tmp)
        self.patched(gst_mail.report_job, build_report=fake_build_report)
        self.patched(gst_mail.erp_ageing, oracle_config=lambda: "cfg")
        with self.assertRaises(_Stop):
            gst_mail.build({**SNAPSHOT, "as_on": as_on})
        return seen

    def test_today_builds_the_report_exactly_as_it_always_did(self):
        seen = self.run_build(None)
        self.assertEqual(set(seen["kwargs"]), {"progress_cb", "cancel_event"})
        self.assertIn(date.today().isoformat(), seen["name"])

    def test_a_past_date_fetches_the_ageing_as_on_that_date(self):
        fetch = MagicMock(return_value="frame")
        self.patched(erp_ageing, fetch_ageing_from_erp=fetch)
        seen = self.run_build(PAST)
        self.assertIn("2026-09-30", seen["name"])
        self.assertEqual(seen["kwargs"]["ageing"](), "frame")
        self.assertEqual(fetch.call_args.kwargs["as_on"], PAST)


class UnidentifiedTests(_Case):
    def run_build(self, as_on):
        calls = []

        def fake_register(cfg, log, cancel, **kwargs):
            calls.append(kwargs)
            raise _Stop

        self.patched(unidentified_mail, SCRATCH_DIR=self.tmp)
        self.patched(unidentified_mail, fetch_register_from_erp=fake_register)
        self.patched(unidentified_mail, oracle_config=lambda: "cfg")
        with self.assertRaises(_Stop):
            unidentified_mail.build({**SNAPSHOT, "as_on": as_on})
        return calls[0]

    def test_today_asks_for_the_register_as_it_is_now(self):
        self.assertEqual(self.run_build(None), {})

    def test_a_past_date_asks_for_the_register_as_on_that_date(self):
        self.assertEqual(self.run_build(PAST), {"as_on": PAST})


if __name__ == "__main__":
    unittest.main()
