"""Meeting Debtors Reports Auto Mailer: the as-on choice (a date, or the last day of last month), the
point-in-time ERP queries it asks for, and that every report is built as on that date."""

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, jobs, permissions
from app.database import Base
from app.jobs import JobUserError
from app.services import erp_ageing
from app.services import scheduled_report_mail as mail
from app.services.debtors_mailer import auto_mail as debtors
from app.services.meeting_debtors import as_on, auto_mail
from app.services.meeting_debtors.models import MeetingAsOn
from app.services.unapplied_receipts import erp_register

KEY = auto_mail.APP_KEY


class LastMonthEndTests(unittest.TestCase):
    def test_it_is_the_last_day_of_the_month_before(self):
        for today, expected in (
            (date(2026, 11, 13), date(2026, 10, 31)),
            (date(2026, 12, 1), date(2026, 11, 30)),    # the first of the month: the day before it
            (date(2026, 3, 31), date(2026, 2, 28)),
            (date(2028, 3, 1), date(2028, 2, 29)),       # a leap year
            (date(2027, 1, 15), date(2026, 12, 31)),     # across the year
        ):
            self.assertEqual(as_on.last_month_end(today), expected, today)

    def test_that_last_day_is_included_in_the_queries_because_they_ask_for_on_or_before(self):
        sql = erp_ageing.ageing_as_on_sql()
        self.assertIn("p.gl_date <= :as_on", sql)
        self.assertIn("a.gl_date <= :as_on", sql)


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[MeetingAsOn.__table__])
        self.factory = sessionmaker(bind=engine)
        self.db = self.factory()
        self.addCleanup(self.db.close)
        self.today = date(2026, 11, 13)

    def test_nothing_saved_means_last_month_end(self):
        view = as_on.get_setting(self.db, KEY, self.today)
        self.assertEqual((view["mode"], view["resolved_date"]), ("last_month_end", "2026-10-31"))

    def test_a_picked_date_is_used_as_it_is(self):
        view = as_on.save_setting(self.db, KEY, "date", date(2026, 9, 15), "a@b.in", self.today)
        self.assertEqual((view["mode"], view["as_on_date"], view["resolved_date"]), ("date", "2026-09-15", "2026-09-15"))

    def test_today_is_allowed_but_not_a_future_date(self):
        self.assertEqual(as_on.save_setting(self.db, KEY, "date", self.today, None, self.today)["resolved_date"],
                         "2026-11-13")
        with self.assertRaisesRegex(as_on.AsOnError, "future"):
            as_on.save_setting(self.db, KEY, "date", date(2026, 11, 14), None, self.today)

    def test_a_date_choice_needs_a_date_and_the_mode_must_be_known(self):
        with self.assertRaisesRegex(as_on.AsOnError, "Pick the date"):
            as_on.save_setting(self.db, KEY, "date", None, None, self.today)
        with self.assertRaises(as_on.AsOnError):
            as_on.save_setting(self.db, KEY, "yesterday", None, None, self.today)

    def test_switching_to_last_month_end_follows_the_run_date_and_keeps_the_picked_date(self):
        as_on.save_setting(self.db, KEY, "date", date(2026, 9, 15), None, self.today)
        view = as_on.save_setting(self.db, KEY, "last_month_end", None, None, self.today)
        self.assertEqual((view["mode"], view["resolved_date"], view["as_on_date"]), ("last_month_end", "2026-10-31", "2026-09-15"))
        # worked out afresh at every run
        self.assertEqual(as_on.get_setting(self.db, KEY, date(2026, 12, 2))["resolved_date"], "2026-11-30")

    def test_a_saved_date_that_is_no_longer_usable_is_reported_not_guessed(self):
        self.db.add(MeetingAsOn(app_key=KEY, mode="date", as_on_date=date(2026, 12, 31)))
        self.db.commit()
        view = as_on.get_setting(self.db, KEY, self.today)
        self.assertIsNone(view["resolved_date"])
        self.assertIn("future", view["problem"])
        with patch.object(as_on, "SessionLocal", self.factory), self.assertRaises(as_on.AsOnError):
            as_on.current(KEY, self.today)

    def test_current_reads_the_saved_choice_for_a_run(self):
        as_on.save_setting(self.db, KEY, "date", date(2026, 8, 31), None, self.today)
        with patch.object(as_on, "SessionLocal", self.factory):
            self.assertEqual(as_on.current(KEY, self.today), ("date", date(2026, 8, 31)))


class MailerWiringTests(unittest.TestCase):
    def test_the_meeting_mail_is_built_as_on_the_chosen_date(self):
        seen = {}

        def fake_build(snapshot, progress_cb=None, cancel_event=None, *, as_on=None):
            seen["as_on"] = as_on
            return mail.MailPayload(values={"date": "x"})

        with patch.object(as_on, "current", lambda key: ("date", date(2026, 9, 30))), \
                patch.object(debtors, "build", fake_build):
            payload = auto_mail.build({"send_when_unmapped": True})
        self.assertEqual(seen["as_on"], date(2026, 9, 30))
        self.assertEqual(payload.detail["as_on_mode"], "date")

    def test_nothing_usable_saved_stops_the_run_with_a_plain_message(self):
        def broken(key):
            raise as_on.AsOnError("The date the reports are as on cannot be in the future.")

        with patch.object(as_on, "current", broken), self.assertRaisesRegex(JobUserError, "future"):
            auto_mail.build({"send_when_unmapped": True})

    def test_the_debtors_mailer_itself_is_still_as_on_today(self):
        """Without an as-on date the Debtors Reports Auto Mailer builds exactly as before (live ageing, no date asked)."""
        calls = self._run_build(None)
        self.assertEqual(calls["ageing"], [{}])                       # fetch_ageing_from_erp gets no as_on
        self.assertEqual(calls["register"], [None])
        self.assertEqual(calls["values_date"], date.today().strftime("%d-%b-%Y"))

    def test_every_report_is_asked_for_the_date(self):
        calls = self._run_build(date(2026, 10, 31))
        self.assertEqual(calls["ageing"], [{"as_on": date(2026, 10, 31)}])    # fetched once, shared by all three
        self.assertEqual(calls["register"], [date(2026, 10, 31)])
        self.assertEqual(calls["untagged"], [date(2026, 10, 31)])
        self.assertEqual(calls["unapplied"], [date(2026, 10, 31)])
        self.assertEqual(calls["values_date"], "31-Oct-2026")
        self.assertEqual(calls["detail_date"], "2026-10-31")

    def _run_build(self, as_on_date):
        """build() with the ERP and the report writers replaced; it stops at the mapping hold, which is
        after every report was asked for and before any workbook is needed."""
        calls = {"ageing": [], "register": [], "untagged": [], "unapplied": []}
        frame = pd.DataFrame({"Customer Account": ["1"]})

        def fake_ageing(cfg, log_q=None, progress_cb=None, cancel_event=None, include_buckets=True, **extra):
            calls["ageing"].append(extra)
            return frame

        def fake_register(cfg, log, cancel, **extra):
            calls["register"].append(extra.get("as_on"))
            return pd.DataFrame()

        def fake_gst(path, name, progress_cb=None, cancel_event=None, ageing=None, look="default"):
            ageing()
            return SimpleNamespace(result={"total": 1, "found": 1, "blank": 0}, df=frame, gst_map={})

        def fake_untagged(path, **kw):
            kw["ageing"]()
            calls["untagged"].append(kw.get("as_on_date"))
            return SimpleNamespace(result={"needs_mapping_fix": True, "validation_warnings": [
                {"category": "Location Not Mapped", "items": ["X"]}]})

        def fake_unapplied(path, cfg, maps, register, ageing, regions, **kw):
            calls["unapplied"].append(kw.get("as_on_date"))
            return {"needs_mapping_fix": True, "validation_warnings": []}

        class FakeRegions:
            def __init__(self, cfg, cancel):
                pass

            def result(self, *a):
                return {}

        class FakeSession:
            def close(self):
                pass

        with patch.object(debtors, "SessionLocal", FakeSession), \
                patch.object(debtors.mapping_store, "load_all", lambda db: ({}, {})), \
                patch.object(debtors.erp_ageing, "fetch_ageing_from_erp", fake_ageing), \
                patch.object(debtors.erp_ageing, "oracle_config", lambda: object()), \
                patch.object(debtors, "fetch_register_from_erp", fake_register), \
                patch.object(debtors.customer_region, "RegionFetch", FakeRegions), \
                patch.object(debtors.gst_job, "build_report", fake_gst), \
                patch.object(debtors.untagged_job, "build_report", fake_untagged), \
                patch.object(debtors.unapplied_job, "build_report", fake_unapplied):
            payload = debtors.build({"send_when_unmapped": False}, as_on=as_on_date)
        self.assertIsNotNone(payload.hold_reason)
        calls["values_date"] = payload.values["date"]
        calls["detail_date"] = payload.detail["as_on_date"]
        return calls


class PointInTimeQueryTests(unittest.TestCase):
    def test_the_as_on_ageing_counts_from_the_ledger_up_to_the_date(self):
        sql = erp_ageing.ageing_as_on_sql()
        self.assertNotIn("SYSDATE", sql)
        self.assertIn("TRUNC(SYSDATE)".replace("TRUNC(SYSDATE)", ":as_on") + " - apsa.trx_date over_due_days", sql)
        self.assertIn("p.gl_date_closed > :as_on", sql)        # still open on that date
        self.assertNotIn("MOD(", sql)

    def test_a_slice_adds_the_schedule_id_and_only_the_slice(self):
        sql = erp_ageing.ageing_as_on_sql(2, 4)
        self.assertIn("apsa.payment_schedule_id ps_id", sql)
        self.assertIn("MOD(p.payment_schedule_id, 4) = 2", sql)

    def test_the_live_ageing_query_is_untouched(self):
        self.assertIn("TRUNC(SYSDATE) - apsa.trx_date over_due_days", erp_ageing.ERP_AGEING_SQL)
        self.assertNotIn(":as_on", erp_ageing.ERP_AGEING_SQL)

    def test_the_register_is_filtered_by_gl_date_only_when_a_date_is_given(self):
        self.assertNotIn(":as_on", erp_register.register_sql())
        self.assertIn("AND app.gl_date <= :as_on", erp_register.register_sql(date(2026, 10, 31)))

    def test_the_fetch_binds_the_date_and_every_slice_gets_it(self):
        seen = []

        def fake_rows(cfg, sql, cancel_event, on_rows=None, binds=None):
            seen.append((sql, binds))
            columns = ["PS_ID", "CUSTOMER_NAME", "CUSTOMER_ACCOUNT", "LOCATION", "SALESPERSON", "CREDITLIMIT",
                       "PAYMENT_TERM", "TRX_NUMBER", "DUE_DATE", "INVOICE_RCPT_CLASS", "OUTSTANDING_AMOUNT",
                       "OVER_DUE_DAYS"]
            row = (1, "C", "1001", "SITE", "SP", 0, 30, "T1", datetime(2026, 9, 1), "Invoice", 10.0, 5)
            return columns, [row]

        for parts in (1, 4):
            seen.clear()
            with patch.object(config, "ORACLE_FETCH_PARALLELISM", parts), \
                    patch.object(erp_ageing, "_init_oracle_client"), \
                    patch.object(erp_ageing, "_fetch_rows", fake_rows):
                erp_ageing.fetch_ageing_from_erp(erp_ageing.oracle_config(), include_buckets=False,
                                                 as_on=date(2026, 10, 31))
            self.assertEqual(len(seen), parts)
            for sql, binds in seen:
                self.assertEqual(binds, {"as_on": datetime(2026, 10, 31)})
                self.assertIn(":as_on", sql)
                self.assertNotIn("SYSDATE", sql)


class RegistrationTests(unittest.TestCase):
    def test_it_is_a_registered_application_with_its_own_recipients_and_schedule(self):
        self.assertIn(KEY, permissions.APP_KEYS)
        self.assertEqual(permissions.APP_LABELS[KEY], "Meeting Debtors Reports Auto Mailer")
        self.assertIn(KEY, permissions.REPORT_RECIPIENT_APP_KEYS)
        spec, original = mail.SPECS[KEY], mail.SPECS["debtors-reports-auto-mailer"]
        self.assertEqual((spec.default_subject, spec.default_intro, spec.placeholders, spec.fixed_sender),
                         (original.default_subject, original.default_intro, original.placeholders, True))
        self.assertTrue(spec.scheduled)
        self.assertEqual(mail._job_for(KEY), auto_mail.run_auto_mail_job)

    def test_its_jobs_are_allowed_and_share_the_gst_oracle_allowance(self):
        for task in ("app.services.meeting_debtors.auto_mail:run_auto_mail_job",
                     "app.services.meeting_debtors.auto_mail:run_preview_job"):
            self.assertIn(task, jobs._ALLOWED_TASKS)
            self.assertEqual(jobs._RESOURCE_BY_TASK[task], "oracle-gst")

    def test_its_routes_are_the_debtors_routes_plus_the_as_on_setting(self):
        from app.main import app

        def paths(app_key):
            prefix = f"/api/tools/{app_key}"
            return {(path[len(prefix):], method)
                    for path, item in app.openapi()["paths"].items() if path.startswith(prefix)
                    for method in item}

        mine, theirs = paths(KEY), paths("debtors-reports-auto-mailer")
        self.assertTrue(theirs)
        self.assertTrue(theirs <= mine)
        self.assertEqual({path for path, _ in mine - theirs}, {"/as-on"})


if __name__ == "__main__":
    unittest.main()
