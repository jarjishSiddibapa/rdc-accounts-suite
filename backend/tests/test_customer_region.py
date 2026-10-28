"""The customer's ERP region as the Location, the mappings as the fallback, the
one-time move of stored mappings to the ERP's region names, and the ageing
query fetched in slices."""

import threading
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config
from app.database import Base
from app.jobs import JobCancelled, JobUserError
from app.services import customer_region as cr
from app.services import erp_ageing
from app.services.untagged_invoices import processor
from app.services.untagged_invoices.models import AccountsInchargeMap, LocationMap


class NamesAndAccountsTests(unittest.TestCase):
    def test_region_spelling_is_tolerant_but_new_regions_keep_their_own(self):
        self.assertEqual(cr.canonical_region("  odisha-  COMMERCIAL "), "Odisha- Commercial")
        self.assertEqual(cr.canonical_region("MUMBAI"), "Mumbai")
        self.assertEqual(cr.canonical_region("Tamil   Nadu"), "Tamil Nadu")

    def test_accounts_compare_whatever_way_they_were_exported(self):
        for raw in ("24455", " 24455 ", "24455.0", "0024455", 24455, 24455.0):
            self.assertEqual(cr.normalise_account(raw), "24455", raw)
        for blank in (None, "", "nan", float("nan")):
            self.assertEqual(cr.normalise_account(blank), "")
        self.assertEqual(cr.normalise_account("C-77"), "C-77")

    def test_regions_from_rows(self):
        got = cr.regions_from_rows([("24455", "MUMBAI"), (7, "Goa"), (None, "Pune"), ("9", None)])
        self.assertEqual(got, {"24455": "Mumbai", "7": "Goa"})


class LocationFromRegionTests(unittest.TestCase):
    def test_erp_region_wins_and_the_mapping_is_the_fallback(self):
        accounts = pd.Series(["24455", "9", "", "31"])
        fallback = pd.Series(["NCR", "KERALA", "", ""])
        location, from_erp = cr.location_with_regions(accounts, fallback, {"24455": "Mumbai", "31": "Goa"})
        self.assertEqual(location.tolist(), ["Mumbai", "KERALA", "", "Goa"])
        self.assertEqual(from_erp.tolist(), [True, False, False, True])

    def test_without_regions_the_fallback_is_untouched(self):
        fallback = pd.Series(["NCR", ""])
        location, from_erp = cr.location_with_regions(pd.Series(["1", "2"]), fallback, {})
        self.assertEqual(location.tolist(), ["NCR", ""])
        self.assertFalse(from_erp.any())


def _ageing(rows):
    return pd.DataFrame(rows, columns=["Customer Account", "Location Name"])


class UntaggedUsesRegionsTests(unittest.TestCase):
    def test_location_comes_from_the_customer_and_needs_no_mapping(self):
        df = _ageing([("24455", "SOME NEW SITE"), ("9", "OLD SITE"), ("10", "UNMAPPED SITE")])
        location_map = {"OLD SITE": "KERALA"}
        regions = {"24455": "Mumbai"}
        out = processor.add_location_column(df, location_map, regions)
        self.assertEqual(out[processor.LOCATION_COL].tolist(), ["Mumbai", "KERALA", ""])
        # only the customer with neither an ERP region nor a mapping is a gap
        self.assertEqual(processor.missing_location_mappings(df, location_map, regions), ["UNMAPPED SITE"])
        self.assertEqual(processor.count_rows_from_erp_region(df, regions), 1)

    def test_without_regions_it_behaves_as_before(self):
        df = _ageing([("24455", "OLD SITE"), ("10", "UNMAPPED SITE")])
        self.assertEqual(
            processor.add_location_column(df, {"OLD SITE": "KERALA"})[processor.LOCATION_COL].tolist(),
            ["KERALA", ""])
        self.assertEqual(processor.missing_location_mappings(df, {"OLD SITE": "KERALA"}), ["UNMAPPED SITE"])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[LocationMap.__table__, AccountsInchargeMap.__table__])
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)

    def _seed(self):
        self.db.add_all([
            LocationMap(location_name="BHARUCH", location="GUJ-1"),
            LocationMap(location_name="SURAT", location="GUJ-2"),
            LocationMap(location_name="DELHI", location="NCR"),
            LocationMap(location_name="HO MUMBAI", location="UNIDENTIFIED COLL"),
            LocationMap(location_name="THANE", location="MUMBAI"),
            AccountsInchargeMap(location="MUMBAI", accounts_incharge="Haresh"),
            AccountsInchargeMap(location="GUJ-1", accounts_incharge="Dinesh"),
            AccountsInchargeMap(location="GUJ-2", accounts_incharge="Dinesh"),
            AccountsInchargeMap(location="NCR", accounts_incharge="Dharampal"),
            AccountsInchargeMap(location="UNIDENTIFIED COLL", accounts_incharge="Someone"),
        ])
        self.db.commit()

    def _active(self, model, key, value):
        return {getattr(r, key): getattr(r, value)
                for r in self.db.query(model).filter(model.is_deleted == False).all()}  # noqa: E712

    def test_stored_mappings_move_to_the_erp_region_names_once(self):
        self._seed()
        self.assertTrue(cr.migrate_mappings_to_erp_regions(self.db, LocationMap, AccountsInchargeMap))
        self.db.commit()

        locations = self._active(LocationMap, "location_name", "location")
        self.assertEqual(locations, {"BHARUCH": "Gujarat", "SURAT": "Gujarat", "DELHI": "Delhi",
                                     "THANE": "Mumbai",
                                     "HO MUMBAI": "UNIDENTIFIED COLL"})   # not a region: left alone
        incharges = self._active(AccountsInchargeMap, "location", "accounts_incharge")
        # an old row spelt like its region is renamed, not duplicated
        self.assertEqual(incharges["Mumbai"], "Haresh")
        self.assertNotIn("MUMBAI", incharges)
        # 5 old rows (one renamed) + the two new regions, Gujarat and Delhi
        self.assertEqual(self.db.query(AccountsInchargeMap).count(), 7)
        self.assertEqual(incharges["Gujarat"], "Dinesh")
        self.assertEqual(incharges["Delhi"], "Dharampal")
        self.assertNotIn("GUJ-1", incharges)          # nothing uses the old names now
        self.assertNotIn("NCR", incharges)
        self.assertIn("UNIDENTIFIED COLL", incharges)  # still used, so it stays

        # a second run changes nothing, even if an old name is typed in later
        self.db.add(LocationMap(location_name="VAPI", location="GUJ-1"))
        self.db.commit()
        self.assertFalse(cr.migrate_mappings_to_erp_regions(self.db, LocationMap, AccountsInchargeMap))
        self.assertEqual(self._active(LocationMap, "location_name", "location")["VAPI"], "GUJ-1")

    def test_an_empty_database_is_left_alone(self):
        self.assertFalse(cr.migrate_mappings_to_erp_regions(self.db, LocationMap, AccountsInchargeMap))

    def test_an_edited_incharge_carries_over(self):
        self._seed()
        row = self.db.query(AccountsInchargeMap).filter_by(location="NCR").one()
        row.accounts_incharge = "Edited Name"
        self.db.commit()
        cr.migrate_mappings_to_erp_regions(self.db, LocationMap, AccountsInchargeMap)
        self.db.commit()
        self.assertEqual(self._active(AccountsInchargeMap, "location", "accounts_incharge")["Delhi"], "Edited Name")


class _FakeCursor:
    def __init__(self, conn):
        self.conn, self.arraysize = conn, 0

    def execute(self, sql):
        self.conn.queries.append(sql)
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("ORA-boom")
        part = int(sql.rsplit("= ", 1)[1].strip()) if "MOD(" in sql else None
        self.description = [(c,) for c in self.conn.columns]
        self.pending = [r for r in self.conn.rows if part is None or r[0] % self.conn.parts == part]

    def fetchmany(self, n):
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def close(self):
        pass


class _FakeConn:
    columns = ["PS_ID", "CUSTOMER_NAME", "CUSTOMER_ACCOUNT", "LOCATION", "SALESPERSON", "CREDITLIMIT",
               "PAYMENT_TERM", "TRX_NUMBER", "DUE_DATE", "INVOICE_RCPT_CLASS", "OUTSTANDING_AMOUNT", "OVER_DUE_DAYS"]
    parts = 4
    queries: list = []
    fail_on = None
    rows = [(i, f"C{i}", str(1000 + i), "SITE", "SP", 0, 30, f"T{i}", None, "Invoice", 10.0 + i, 5)
            for i in range(1, 41)]

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        pass


class ParallelAgeingFetchTests(unittest.TestCase):
    def _run(self, parts, fail_on=None):
        _FakeConn.queries = []
        _FakeConn.parts = parts
        _FakeConn.fail_on = fail_on
        self.addCleanup(setattr, _FakeConn, "fail_on", None)
        with patch.object(config, "ORACLE_FETCH_PARALLELISM", parts), \
                patch.object(erp_ageing, "_init_oracle_client"), \
                patch.object(erp_ageing, "_connect_oracle", lambda cfg: _FakeConn()):
            return erp_ageing.fetch_ageing_from_erp(erp_ageing.oracle_config(), include_buckets=False)

    def test_slices_together_are_the_whole_ageing_in_schedule_order(self):
        sliced = self._run(4)
        self.assertEqual(len(_FakeConn.queries), 4)
        self.assertEqual(sorted(q.rsplit("= ", 1)[1].strip() for q in _FakeConn.queries), ["0", "1", "2", "3"])
        self.assertTrue(all("MOD(apsa.payment_schedule_id, 4)" in q for q in _FakeConn.queries))
        self.assertEqual(sliced["Customer Account"].tolist(), [str(1000 + i) for i in range(1, 41)])

        single = self._run(1)
        self.assertEqual(len(_FakeConn.queries), 1)
        self.assertNotIn("MOD(", _FakeConn.queries[0])
        self.assertEqual(single["Customer Account"].tolist(), sliced["Customer Account"].tolist())

    def test_a_failing_slice_is_reported_as_the_ageing_query_failing(self):
        with self.assertRaisesRegex(JobUserError, "ERP ageing query failed: ORA-boom"):
            self._run(4, fail_on="= 2")

    def test_cancelling_stops_the_fetch(self):
        stop = threading.Event()
        stop.set()
        with patch.object(config, "ORACLE_FETCH_PARALLELISM", 4), \
                patch.object(erp_ageing, "_init_oracle_client"), \
                patch.object(erp_ageing, "_connect_oracle", lambda cfg: _FakeConn()):
            with self.assertRaises(JobCancelled):
                erp_ageing.fetch_ageing_from_erp(erp_ageing.oracle_config(), cancel_event=stop)

    def test_the_partition_sql_adds_only_the_schedule_id_and_the_slice(self):
        sql = erp_ageing._partition_sql(1, 4)
        self.assertIn("apsa.payment_schedule_id ps_id,", sql)
        self.assertTrue(sql.rstrip().endswith("MOD(apsa.payment_schedule_id, 4) = 1"))
        self.assertEqual(sql.count("SELECT\n    apsa.payment_schedule_id ps_id"), 1)


if __name__ == "__main__":
    unittest.main()
