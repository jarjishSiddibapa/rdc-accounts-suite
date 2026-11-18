"""The Debtors mailers' choice of which reports go out, the mail wording that follows it, and the Accounts
Incharge column the Debtors reports carry right after Location."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services import scheduled_report_mail as mail
from app.services.debtors_mailer import auto_mail as debtors
from app.services.debtors_mailer import report_choice
from app.services.debtors_mailer.models import ReportChoice
from app.services.unapplied_receipts import processor as unapplied_processor
from app.services.untagged_invoices import processor as untagged_processor

KEY = "meeting-debtors-reports-auto-mailer"
SPEC = mail.SPECS[KEY]


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[ReportChoice.__table__])
        self.factory = sessionmaker(bind=engine)
        self.db = self.factory()
        self.addCleanup(self.db.close)

    def test_nothing_saved_means_all_three_as_it_always_was(self):
        view = report_choice.get_setting(self.db, KEY)
        self.assertEqual(view["selected"], ["ageing", "unapplied", "untagged"])
        self.assertTrue(view["all_selected"])

    def test_a_choice_is_saved_per_application(self):
        report_choice.save_setting(self.db, KEY, ["unapplied"], "a@b.in")
        self.assertEqual(report_choice.get_setting(self.db, KEY)["selected"], ["unapplied"])
        self.assertTrue(report_choice.get_setting(self.db, "debtors-reports-auto-mailer")["all_selected"])

    def test_at_least_one_report_and_only_known_ones(self):
        with self.assertRaisesRegex(report_choice.ReportChoiceError, "at least one"):
            report_choice.save_setting(self.db, KEY, [], None)
        with self.assertRaisesRegex(report_choice.ReportChoiceError, "Unknown"):
            report_choice.save_setting(self.db, KEY, ["ageing", "nope"], None)

    def test_a_run_reads_the_saved_choice_in_the_mails_order(self):
        report_choice.save_setting(self.db, KEY, ["untagged", "ageing"], None)
        with patch.object(report_choice, "SessionLocal", self.factory):
            self.assertEqual(report_choice.current(KEY), ("ageing", "untagged"))


class WordingTests(unittest.TestCase):
    def test_all_three_leave_the_standard_wording_untouched(self):
        everything = report_choice.ALL_REPORTS
        self.assertEqual(report_choice.adapt_subject(SPEC.default_subject, SPEC.default_subject, everything),
                         SPEC.default_subject)
        self.assertEqual(report_choice.adapt_intro(SPEC.default_intro, everything), SPEC.default_intro)

    def test_the_subject_names_only_what_is_sent(self):
        subject = report_choice.adapt_subject(SPEC.default_subject, SPEC.default_subject, ("unapplied", "untagged"))
        self.assertEqual(subject, "Unapplied Receipts, Untagged Invoices and Below 1k Invoices as on ({date})")
        only = report_choice.adapt_subject(SPEC.default_subject, SPEC.default_subject, ("ageing",))
        self.assertEqual(only, "Ageing with GST Invoice Numbers as on ({date})")

    def test_a_subject_a_person_wrote_is_left_alone(self):
        self.assertEqual(report_choice.adapt_subject("Meeting pack {date}", SPEC.default_subject, ("ageing",)),
                         "Meeting pack {date}")

    def test_the_list_is_renumbered_and_the_attached_sentence_names_what_is_attached(self):
        intro = report_choice.adapt_intro(SPEC.default_intro, ("unapplied", "untagged"))
        self.assertIn("1. Unapplied Receipts as on {date}", intro)
        self.assertIn("2. Untagged Invoices as on {date}", intro)
        self.assertIn("3. Below 1k Invoices as on {date}", intro)
        self.assertNotIn("Ageing with GST", intro)
        self.assertIn("The Unapplied Receipts report and the Untagged Invoices report are attached.", intro)

    def test_one_report_is_singular(self):
        intro = report_choice.adapt_intro(SPEC.default_intro, ("ageing",))
        self.assertIn("1. Ageing with GST invoice numbers as on {date}", intro)
        self.assertNotIn("2.", intro)
        self.assertIn("The ageing with the GST invoice numbers is attached.", intro)


class OnlyThePickedReportsAreBuiltTests(unittest.TestCase):
    def _build(self, selected):
        built = []
        frame = pd.DataFrame({"Customer Account": ["1"]})

        def gst(path, name, **kw):
            built.append("ageing")
            return SimpleNamespace(result={"total": 1, "found": 1, "blank": 0}, df=frame, gst_map={})

        def untagged(path, **kw):
            built.append("untagged")
            return SimpleNamespace(result={"needs_mapping_fix": True, "validation_warnings": []})

        def unapplied(path, *a, **kw):
            built.append("unapplied")
            return {"needs_mapping_fix": True, "validation_warnings": []}

        class FakeRegions:
            def __init__(self, cfg, cancel):
                pass

            def result(self, *a):
                return {}

        class FakeSession:
            def close(self):
                pass

        registers = []
        with patch.object(debtors.report_choice, "current", lambda key: selected), \
                patch.object(debtors, "SessionLocal", FakeSession), \
                patch.object(debtors.mapping_store, "load_all", lambda db: ({}, {})), \
                patch.object(debtors, "_fit_attachments", lambda paths, *a, **k: ([[str(p) for p in paths]], "ok")), \
                patch.object(debtors.erp_ageing, "oracle_config", lambda: object()), \
                patch.object(debtors.erp_ageing, "fetch_ageing_from_erp", lambda *a, **k: frame), \
                patch.object(debtors, "fetch_register_from_erp", lambda *a, **k: registers.append(1)), \
                patch.object(debtors.customer_region, "RegionFetch", FakeRegions), \
                patch.object(debtors.gst_job, "build_report", gst), \
                patch.object(debtors.untagged_job, "build_report", untagged), \
                patch.object(debtors.unapplied_job, "build_report", unapplied):
            payload = debtors.build({"send_when_unmapped": False}, app_key=KEY)
        return built, payload, registers

    def test_only_the_picked_reports_are_built(self):
        built, payload, registers = self._build(("unapplied",))
        self.assertEqual(built, ["unapplied"])
        self.assertEqual(payload.detail["reports"], ["unapplied"])
        self.assertEqual(len(registers), 1)

    def test_a_report_left_out_is_not_even_fetched(self):
        built, _, registers = self._build(("ageing",))
        self.assertEqual(built, ["ageing"])
        self.assertEqual(registers, [])               # the unapplied register is not fetched for the ageing alone

    def test_all_three_are_built_when_all_are_picked(self):
        built, payload, _ = self._build(report_choice.ALL_REPORTS)
        self.assertEqual(sorted(built), ["ageing", "unapplied", "untagged"])
        self.assertIn("reports", payload.detail)

    def test_the_payload_rewords_the_mail_for_what_was_picked(self):
        _, payload, _ = self._build(("ageing", "unapplied"))
        subject, intro = payload.reword(SPEC.default_subject, SPEC.default_intro)
        self.assertEqual(subject, "Ageing with GST Invoice Numbers and Unapplied Receipts as on ({date})")
        self.assertNotIn("Untagged", intro)


class AccountsInchargeColumnTests(unittest.TestCase):
    def test_untagged_ageing_gets_it_right_after_location(self):
        df = pd.DataFrame({"Customer Name": ["A", "B"], "Location Name": ["X", "Y"], "Location": ["Gujarat", "Delhi"],
                           "Accounted Outstanding": [1.0, 2.0]})
        out = untagged_processor.add_incharge_column(df, {"Gujarat": "Ravi"})
        self.assertEqual(list(out.columns), ["Customer Name", "Location Name", "Location", "Accounts Incharge",
                                             "Accounted Outstanding"])
        self.assertEqual(out["Accounts Incharge"].tolist(), ["Ravi", ""])          # blank where nothing is mapped

    def test_the_gst_ageing_gets_it_after_location_name_from_the_same_location_as_the_other_reports(self):
        df = pd.DataFrame({"Customer Account": ["1", "2", "3"], "Location Name": ["SITE A", "SITE B", "SITE C"],
                           "Invoice No/ Receipt No": ["i1", "i2", "i3"]})
        out = untagged_processor.add_incharge_after_location_name(
            df, {"SITE B": "Surat", "SITE C": "Mumbai"}, {"Gujarat": "Ravi", "Surat": "Meena"}, {"1": "Gujarat"})
        self.assertEqual(list(out.columns), ["Customer Account", "Location Name", "Accounts Incharge",
                                             "Invoice No/ Receipt No"])
        # row 1: the customer's ERP region; row 2: the mapped Location Name; row 3: Mumbai has no incharge
        self.assertEqual(out["Accounts Incharge"].tolist(), ["Ravi", "Meena", ""])
        self.assertNotIn("Accounts Incharge", df.columns)                           # the caller's frame is untouched

    def test_the_unapplied_sheets_get_it_right_after_location_before_sales_person(self):
        df = pd.DataFrame({"Customer Number": [1, 2], "Payment Date": ["01-Oct-26", "02-Oct-26"],
                           "Location": ["Gujarat", "Delhi"], "Sales Person": ["S1", "S2"]})
        out = unapplied_processor._with_incharge(df, {"Gujarat": "Ravi"})
        self.assertEqual(list(out.columns), ["Customer Number", "Payment Date", "Location", "Accounts Incharge",
                                             "Sales Person"])
        self.assertEqual(out["Accounts Incharge"].tolist(), ["Ravi", ""])

    def test_the_standalone_untagged_and_unapplied_tools_are_unchanged_by_default(self):
        import inspect
        from app.services.untagged_invoices import report_job as untagged_job
        from app.services.unapplied_receipts import report_job as unapplied_job
        self.assertIs(inspect.signature(untagged_job.build_report).parameters["incharge_column"].default, False)
        self.assertIs(inspect.signature(unapplied_job.build_report).parameters["incharge_column"].default, False)
        self.assertIs(inspect.signature(unapplied_processor.write_formatted_excel).parameters["incharge_column"].default, False)


if __name__ == "__main__":
    unittest.main()
