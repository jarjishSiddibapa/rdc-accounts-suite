"""The date the Debtors reports are as on: the point-in-time ERP queries a past date asks for, and that every report
of a mail plan is built as on that date (and the live ageing, as always, when the plan is as on today)."""

import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app import config
from app.services import erp_ageing
from app.services.debtors_mailer import auto_mail as debtors
from app.services.unapplied_receipts import erp_register


class BuilderAsOnTests(unittest.TestCase):
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

        def fake_gst(path, name, progress_cb=None, cancel_event=None, ageing=None, look="default", prepare=None,
                     gst_map=None):
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
            payload = debtors.build({"send_when_unmapped": False, "as_on": as_on_date})
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


class RoutesTests(unittest.TestCase):
    def test_the_mail_is_set_up_per_schedule_and_the_application_has_no_schedule_of_its_own(self):
        from app.main import app

        prefix = "/api/tools/debtors-reports-auto-mailer"
        paths = {(path[len(prefix):], method)
                 for path, item in app.openapi()["paths"].items() if path.startswith(prefix) for method in item}
        one = "/schedules/{schedule_id}"
        for route in (("/schedules", "get"), ("/schedules", "post"), (one, "get"), (one, "patch"), (one, "delete"),
                      (one + "/auto-mail", "get"), (one + "/auto-mail", "put"), (one + "/auto-mail/log", "get"),
                      (one + "/mail/defaults", "get"), (one + "/mail/preview", "post"),
                      (one + "/mail/confirm-send", "post"), (one + "/mappings/locations", "get"),
                      ("/mappings/locations", "get"), ("/mappings/accounts-incharge", "get")):
            self.assertIn(route, paths)
        # nothing is sent without saying which schedule, and the only way to send one now is the preview
        for route in (("/auto-mail", "get"), ("/auto-mail", "put"), ("/auto-mail/send-now", "post"),
                      ("/mail/preview", "post"), (one + "/auto-mail/send-now", "post"),
                      (one + "/reports", "get"), (one + "/as-on", "get")):
            self.assertNotIn(route, paths)

    def test_the_retired_meeting_application_has_no_routes_and_no_access_key(self):
        from app import permissions
        from app.main import app

        key = "meeting-debtors-reports-auto-mailer"
        self.assertNotIn(key, permissions.APP_KEYS)
        self.assertIn(key, permissions.RETIRED_APP_KEYS)
        self.assertFalse([p for p in app.openapi()["paths"] if key in p])


if __name__ == "__main__":
    unittest.main()
