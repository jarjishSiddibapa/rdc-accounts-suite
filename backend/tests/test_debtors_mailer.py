"""Debtors Reports Auto Mailer: the register fetched from the ERP, one shared
ageing fetch, merged mapping warnings, fitting three workbooks in one mail, and
the mailer's own mapping tables."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, permissions
from app.database import Base
from app.services import erp_ageing
from app.services import scheduled_report_mail as mail
from app.services.debtors_mailer import auto_mail, mapping_store
from app.services.debtors_mailer.models import AccountsInchargeMap, LocationMap
from app.services.unapplied_receipts import erp_register


class RegisterFrameTests(unittest.TestCase):
    COLUMNS = ["CUSTOMER_NAME", "CUSTOMER_NUMBER", "GL_DATE", "BATCH_SOURCE_NAME", "BATCH_NAME", "RECEIPT_METHOD",
               "RECEIPT_NUMBER", "RECEIPT_DATE", "ON_ACCOUNT_AMT", "UNAPPLIED_AMT", "CLAIM_AMT", "CUSTOMER_ID",
               "UNID_FLAG"]

    def test_rows_come_back_in_the_registers_own_shape(self):
        rows = [
            ("J M BUILDERS", "139554", None, None, None, "HDFC CC", "ON/RAI/061026/1",
             pd.Timestamp("2026-10-06"), 0, 0.4, 0, 6090130, None),
            (None, None, None, None, None, "Bandhan", "1048/HO/261225",
             pd.Timestamp("2025-12-26"), 0, 239359, 0, 0, "*"),
        ]
        df = erp_register.register_frame(self.COLUMNS, rows)
        self.assertEqual(list(df.columns), [
            "Customer Name", "Customer Number", "Payment Method", "Payment Number", "Payment Date",
            "On Account/ Prepayment Amount", "Unapplied Amount"])
        self.assertEqual(df["Payment Date"].tolist(), ["06-OCT-26", "26-DEC-25"])
        self.assertEqual(df["Customer Name"].tolist(), ["J M BUILDERS", "***** Unidentified"])
        self.assertEqual(df["Customer Number"].tolist(), ["139554", ""])
        self.assertEqual(df["Unapplied Amount"].tolist(), [0.4, 239359.0])
        self.assertTrue(all(df[c].dtype == float for c in ("On Account/ Prepayment Amount", "Unapplied Amount")))

    def test_the_three_report_parameters_are_left_empty(self):
        sql = erp_register.REGISTER_SQL.format(hint="")
        for bind in (":p_in_format_option", ":nls_NO_BATCH", ":p_in_curr_code"):
            self.assertIn(bind, sql)
        self.assertNotIn(";", sql)

    def test_the_parallel_hint_is_a_setting(self):
        with patch.object(config, "ORACLE_REGISTER_PARALLEL_DEGREE", 4):
            self.assertIn("/*+ PARALLEL(4) */", erp_register.register_sql())
        with patch.object(config, "ORACLE_REGISTER_PARALLEL_DEGREE", 0):
            self.assertNotIn("PARALLEL", erp_register.register_sql())


class SharedAgeingTests(unittest.TestCase):
    def test_the_ageing_is_fetched_once_however_many_reports_ask(self):
        calls = []
        frame = pd.DataFrame({"Customer Account": ["1", "2"]})

        def fake_fetch(cfg, log_q=None, progress_cb=None, cancel_event=None, include_buckets=True):
            calls.append(include_buckets)
            return frame

        shared = auto_mail._SharedAgeing(object(), None)
        results = []
        with patch.object(erp_ageing, "fetch_ageing_from_erp", fake_fetch):
            threads = [threading.Thread(target=lambda: results.append(shared.get())) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(calls, [True])
        self.assertEqual(len(results), 5)
        results[0].loc[0, "Customer Account"] = "changed"      # a report editing its copy...
        self.assertEqual(frame.loc[0, "Customer Account"], "1")  # ...never disturbs the others

    def test_untagged_gets_the_ageing_without_the_erps_own_buckets(self):
        df = pd.DataFrame({"Customer Name": ["A"], "0-30 Days": [1.0], "360+ Days": [2.0], "Days O/S": [5]})
        self.assertEqual(list(auto_mail._without_buckets(df).columns), ["Customer Name", "Days O/S"])


class MergedWarningsTests(unittest.TestCase):
    def test_both_reports_warnings_become_one_list(self):
        location_map = {"MUMBAI-X": "Mumbai"}
        incharge_map = {"Mumbai": "Haresh"}
        untagged = [{"category": "Location Not Mapped", "items": ["NEW PLANT"]},
                    {"category": "Accounts Incharge Not Mapped", "items": ["Goa"]}]
        # the Unapplied report lists a receipt location it cannot map as an unmapped incharge
        unapplied = [{"category": "Accounts Incharge Not Mapped", "items": ["Goa", "Raw Receipt Site"]}]
        merged = auto_mail._merge_warnings(
            untagged, unapplied, location_map=location_map, incharge_map=incharge_map, regions={"Goa"})
        self.assertEqual(merged, [
            {"category": "Location Not Mapped", "items": ["NEW PLANT", "RAW RECEIPT SITE"]},
            {"category": "Accounts Incharge Not Mapped", "items": ["Goa"]},
        ])

    def test_nothing_unmapped_is_nothing(self):
        self.assertEqual(auto_mail._merge_warnings([], None, location_map={}, incharge_map={}), [])
        self.assertIn("Location Not Mapped (1): X", auto_mail._hold_reason(
            [{"category": "Location Not Mapped", "items": ["X"]}]))


class FitAttachmentsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _file(self, name, size):
        path = self.dir / name
        path.write_bytes(b"x" * size)
        return path

    def test_small_files_go_in_one_mail_untouched(self):
        paths = [self._file("a.xlsx", 100), self._file("b.xlsx", 200)]
        groups, how = auto_mail._fit_attachments(paths)
        self.assertEqual(groups, [[str(p) for p in paths]])
        self.assertEqual(how, "complete workbooks")

    def test_too_big_together_but_fine_after_the_largest_is_converted_to_xlsb(self):
        big, small = self._file("big.xlsx", 2_000_000), self._file("small.xlsx", 100)

        def fake_xlsb(source, destination):
            Path(destination).write_bytes(b"y" * 300)
            return True

        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1_000_000), \
                patch.object(auto_mail.xlsx_compact, "compact_xlsx_in_place"), \
                patch.object(auto_mail.xlsb_convert, "convert_to_xlsb", fake_xlsb):
            groups, how = auto_mail._fit_attachments([big, small])
        self.assertEqual(len(groups), 1)
        self.assertEqual([Path(p).name for p in groups[0]], ["big.xlsb", "small.xlsx"])   # order kept
        self.assertIn(".xlsb", how)

    def test_when_even_that_is_not_enough_it_is_several_mails_and_nothing_is_dropped(self):
        a, b, c = (self._file(n, 700_000) for n in ("a.xlsx", "b.xlsx", "c.xlsx"))
        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1_000_000), \
                patch.object(auto_mail.xlsx_compact, "compact_xlsx_in_place"), \
                patch.object(auto_mail.xlsb_convert, "convert_to_xlsb", lambda s, d: False):
            groups, how = auto_mail._fit_attachments([a, b, c])
        self.assertEqual(sorted(Path(p).name for g in groups for p in g), ["a.xlsx", "b.xlsx", "c.xlsx"])
        self.assertEqual(len(groups), 3)
        self.assertIn("3 mails", how)


class MapperAndRegistrationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[LocationMap.__table__, AccountsInchargeMap.__table__])
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)

    def test_the_mailers_own_mappings_are_editable(self):
        mapping_store.upsert_location(self.db, "new plant", "Mumbai")
        mapping_store.upsert_accounts_incharge(self.db, "Mumbai", "Haresh")
        with patch.object(mapping_store, "_maybe_seed"):
            location_map, incharge_map = mapping_store.load_all(self.db)
        self.assertEqual(location_map, {"NEW PLANT": "Mumbai"})
        self.assertEqual(incharge_map, {"Mumbai": "Haresh"})
        self.assertTrue(mapping_store.delete_location(self.db, "New Plant"))
        self.assertFalse(mapping_store.delete_location(self.db, "New Plant"))

    def test_it_is_a_registered_application_with_its_own_recipients_and_schedule(self):
        key = auto_mail.APP_KEY
        self.assertEqual(key, "debtors-reports-auto-mailer")
        self.assertIn(key, permissions.APP_KEYS)
        self.assertEqual(permissions.APP_LABELS[key], "Debtors Reports Auto Mailer")
        self.assertIn(key, permissions.REPORT_RECIPIENT_APP_KEYS)
        spec = mail.SPECS[key]
        self.assertTrue(spec.scheduled)
        mail.validate_template(spec.default_subject, spec.placeholders)
        mail.validate_template(spec.default_intro, spec.placeholders)
        self.assertIs(mail._job_for(key), auto_mail.run_auto_mail_job)

    def test_its_jobs_are_allowed_and_share_the_gst_oracle_allowance(self):
        from app import jobs
        for task in ("run_auto_mail_job", "run_preview_job"):
            name = f"app.services.debtors_mailer.auto_mail:{task}"
            self.assertIn(name, jobs._ALLOWED_TASKS)
            self.assertEqual(jobs._RESOURCE_BY_TASK[name], "oracle-gst")


if __name__ == "__main__":
    unittest.main()
