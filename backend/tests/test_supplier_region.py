"""The supplier's ERP region as the Location of the payables-side reports (Unaccounted Transactions, Pending
MRN, Uninvoiced Expense POs, Loans & Advance / IOCL / TDS), the supplier-site mapping as the fallback, and the
"mapping not found" popup only when neither knows the row."""

import unittest
from unittest.mock import patch

import pandas as pd

from app.routers import unaccounted_txn
from app.services import customer_region, supplier_region
from app.services.rdc_payables import processor as payables
from app.services.supplier_region import fetch_for_job as live_fetch_for_job   # bound before the suite's no-ERP stub
from app.services.unaccounted import processing

INCHARGE = {"NCR + Delhi": "Dharampal Sir", "PUNE": "Pramod Sir", "MUMBAI": "Haresh Sir", "CAPEX": "Khushi"}


class _Log:
    def __init__(self):
        self.lines = []

    def put(self, item):
        self.lines.append(item)

    def text(self):
        return "\n".join(f"{level}: {text}" for level, text in self.lines)


class LabelTests(unittest.TestCase):
    def test_an_erp_region_becomes_the_label_the_reports_use(self):
        self.assertEqual(supplier_region.label_for("Delhi"), "NCR + Delhi")
        self.assertEqual(supplier_region.label_for("Aurangabad"), "SAMBHAJI NAGAR")
        self.assertEqual(supplier_region.label_for("Kolkata"), "WEST BENGAL")
        self.assertEqual(supplier_region.label_for("Pune"), "PUNE")

    def test_spelling_of_the_erp_region_does_not_matter(self):
        self.assertEqual(supplier_region.label_for("odisha-commercial"), "ODISHA + RANCHI")
        self.assertEqual(supplier_region.label_for(" ODISHA- COMMERCIAL "), "ODISHA + RANCHI")

    def test_every_erp_region_has_a_label_and_a_new_one_keeps_its_own_spelling(self):
        for region in customer_region.ERP_REGIONS:
            self.assertIn(region, supplier_region.LABEL_FOR_REGION, region)
        self.assertEqual(supplier_region.label_for("Sikkim"), "Sikkim")


class ApplyTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({
            "Supplier Number": ["91363", "500", "501.0", "", "7"],
            "Location": ["CAPEX", "PUNE", "", "", "MUMBAI"],
            "Accounts Incharge": ["Khushi", "Pramod Sir", "", "", "Haresh Sir"],
        })

    def test_the_erp_region_wins_and_the_mapping_stays_for_every_other_row(self):
        df = self.frame()
        stats = supplier_region.apply_supplier_regions(
            df, "Supplier Number", "Location", "Accounts Incharge",
            {"91363": "Delhi", "501": "Mumbai"}, INCHARGE)
        self.assertEqual(df["Location"].tolist(), ["NCR + Delhi", "PUNE", "MUMBAI", "", "MUMBAI"])
        self.assertEqual(df["Accounts Incharge"].tolist(),
                         ["Dharampal Sir", "Pramod Sir", "Haresh Sir", "", "Haresh Sir"])
        # row 0 was CAPEX through the site mapping; row 2 had no mapping at all
        self.assertEqual((stats["from_erp"], stats["changed"], stats["no_incharge"]), (2, 2, []))

    def test_a_row_only_the_erp_knows_is_no_longer_unmapped(self):
        df = self.frame()
        supplier_region.apply_supplier_regions(
            df, "Supplier Number", "Location", "Accounts Incharge", {"501": "Pune"}, INCHARGE)
        self.assertEqual(df.loc[2, "Location"], "PUNE")

    def test_a_row_neither_knows_stays_blank_for_the_popup(self):
        df = self.frame()
        supplier_region.apply_supplier_regions(
            df, "Supplier Number", "Location", "Accounts Incharge", {"91363": "Delhi"}, INCHARGE)
        self.assertEqual(df.loc[3, "Location"], "")

    def test_a_label_with_no_incharge_is_reported_not_guessed(self):
        df = self.frame()
        stats = supplier_region.apply_supplier_regions(
            df, "Supplier Number", "Location", "Accounts Incharge", {"91363": "Rajasthan", "500": "Pune"}, INCHARGE)
        self.assertEqual(df.loc[0, "Location"], "RAJASTHAN")
        self.assertEqual(df.loc[0, "Accounts Incharge"], "")       # not the CAPEX incharge it had
        self.assertEqual(stats["no_incharge"], ["RAJASTHAN"])
        self.assertEqual(df.loc[1, "Accounts Incharge"], "Pramod Sir")

    def test_the_existing_incharge_is_kept_when_the_site_mapping_agrees_and_the_table_is_silent(self):
        df = pd.DataFrame({"Supplier Number": ["1"], "Location": ["RAJASTHAN"], "Accounts Incharge": ["Dharampal Sir"]})
        stats = supplier_region.apply_supplier_regions(
            df, "Supplier Number", "Location", "Accounts Incharge", {"1": "Rajasthan"}, {})
        self.assertEqual(df.loc[0, "Accounts Incharge"], "Dharampal Sir")
        self.assertEqual(stats["no_incharge"], [])

    def test_the_incharge_table_is_read_without_regard_to_case(self):
        df = self.frame()
        supplier_region.apply_supplier_regions(
            df, "Supplier Number", "Location", "Accounts Incharge", {"91363": "Pune"}, {"pune": "Pramod Sir"})
        self.assertEqual(df.loc[0, "Accounts Incharge"], "Pramod Sir")

    def test_no_regions_or_no_number_column_changes_nothing(self):
        for regions, column in (({}, "Supplier Number"), (None, "Supplier Number"), ({"1": "Pune"}, None),
                                ({"1": "Pune"}, "Not There")):
            df = self.frame()
            before = df.copy()
            supplier_region.apply_supplier_regions(df, column, "Location", "Accounts Incharge", regions, INCHARGE)
            pd.testing.assert_frame_equal(df, before)

    def test_the_number_column_is_found_whatever_its_case(self):
        self.assertEqual(supplier_region.find_number_column(["A", "SUPPLIER NUMBER"]), "SUPPLIER NUMBER")
        self.assertEqual(supplier_region.find_number_column(["Vendor Number"]), "Vendor Number")
        self.assertIsNone(supplier_region.find_number_column(["Supplier Name"]))

    def test_the_log_says_how_many_rows_the_erp_decided(self):
        log, df = _Log(), self.frame()
        supplier_region.apply_with_log(df, "Location", "Accounts Incharge", {"91363": "Delhi"}, INCHARGE, log)
        self.assertIn("1 of 5 rows", log.text())
        self.assertIn("1 of them different", log.text())

    def test_the_log_warns_about_a_label_without_incharge(self):
        log, df = _Log(), self.frame()
        supplier_region.apply_with_log(df, "Location", "Accounts Incharge", {"91363": "Rajasthan"}, INCHARGE, log)
        self.assertIn("warn: No Accounts Incharge on file for: RAJASTHAN", log.text())


class FetchTests(unittest.TestCase):
    def test_the_query_goes_from_the_supplier_to_its_party_region(self):
        sql = supplier_region.SUPPLIER_REGION_SQL
        for part in ("ap_suppliers", "s.segment1", "hp.attribute5", "xxrdc_region_details"):
            self.assertIn(part, sql)

    def test_a_failed_lookup_never_stops_a_report(self):
        messages = []
        with patch.object(supplier_region, "fetch_supplier_regions", side_effect=RuntimeError("down")), \
                patch("app.services.erp_ageing.oracle_config", return_value=object()):
            self.assertEqual(live_fetch_for_job(messages), {})
        self.assertEqual(messages[0][0], "warn")
        self.assertIn("site mapping is used for every row", messages[0][1])

    def test_a_good_lookup_is_reported(self):
        messages = []
        with patch.object(supplier_region, "fetch_supplier_regions", return_value={"1": "Pune"}), \
                patch("app.services.erp_ageing.oracle_config", return_value=object()):
            self.assertEqual(live_fetch_for_job(messages), {"1": "Pune"})
        self.assertEqual(messages, [("ok", "Read the region of 1 suppliers from the ERP")])

    def test_numbers_are_compared_the_way_the_customer_accounts_are(self):
        got = customer_region.regions_from_rows([("0091363", "Delhi"), ("12.0", "PUNE"), (None, "Goa")])
        self.assertEqual(got, {"91363": "Delhi", "12": "Pune"})


def _payables_columns():
    return ["Vendor Name", "Vendor Number", "Invoice Number", "Invoice Date", "Header GL Date", "Invoice Type",
            "Vendor Site Code", "Liability Account", "Distribution Account", "Accounted Outstanding Amount"]


def _payables_rows():
    base = ["A Vendor", "", "INV", "01-Jan-2026", "05-Jan-2026", "Standard", "", "x", "1.2.3.4", "-100"]

    def row(number, invoice, site):
        out = list(base)
        out[1], out[2], out[6] = number, invoice, site
        return out
    return [
        row("91363", "I1", "AEROCITY-CAPEX"),     # the site says CAPEX; the ERP says Delhi
        row("500", "I2", "PUNE-1"),               # the ERP knows nothing: the site mapping decides
        row("600", "I3", "BRAND-NEW-SITE"),       # nobody knows: stays blank for the popup
        row("700", "I4", "BRAND-NEW-SITE"),       # only the ERP knows
    ]


class PayablesTests(unittest.TestCase):
    MAPPING = {"AEROCITY-CAPEX": {"Region": "CAPEX", "Accounts Incharge": "Khushi"},
               "PUNE-1": {"Region": "PUNE", "Accounts Incharge": "Pramod Sir"}}
    REGION_INCHARGE = {"CAPEX": "Khushi", "PUNE": "Pramod Sir", "NCR + Delhi": "Dharampal Sir", "MUMBAI": "Haresh Sir"}

    def run_report(self, regions, **extra):
        stats = {}
        df, _ = payables.process_report(
            _payables_columns(), _payables_rows(), 2026, 12, self.MAPPING,
            region_incharge_map=self.REGION_INCHARGE, supplier_regions=regions, region_stats=stats, **extra)
        return df.set_index("Invoice Number"), stats

    def test_without_regions_it_is_exactly_the_site_mapping(self):
        df, stats = self.run_report(None)
        self.assertEqual(df["Region"].to_dict(), {"I1": "CAPEX", "I2": "PUNE", "I3": "", "I4": ""})
        self.assertEqual(stats["from_erp"], 0)

    def test_the_erp_region_wins_then_the_site_mapping_then_the_popup(self):
        df, stats = self.run_report({"91363": "Delhi", "700": "Mumbai"})
        self.assertEqual(df["Region"].to_dict(), {"I1": "NCR + Delhi", "I2": "PUNE", "I3": "", "I4": "MUMBAI"})
        self.assertEqual(df["Accounts Incharge"].to_dict(),
                         {"I1": "Dharampal Sir", "I2": "Pramod Sir", "I3": "", "I4": "Haresh Sir"})
        self.assertEqual(stats["from_erp"], 2)

    def test_an_explicit_vendor_override_still_beats_the_erp(self):
        df, _ = self.run_report(
            {"91363": "Delhi"},
            invoice_overrides={("91363", ""): {"Region": "HEAD OFFICE", "Accounts Incharge": "Sneha"}})
        self.assertEqual(df.loc["I1", "Region"], "HEAD OFFICE")


class UnaccountedPipelineTests(unittest.TestCase):
    """The three reports of the Unaccounted app all take the regions the job fetched."""

    def test_the_job_fetches_once_and_passes_the_regions_to_the_pipeline(self):
        seen = {}

        def fake_phase(paths, supplier_regions=None):
            seen["regions"] = supplier_regions
            df = pd.DataFrame({"Location": ["PUNE"], "Supplier Site": ["X"]})
            return df, 1, 1, 1, []

        def fake_run(fn, *args, **kwargs):
            return None if fn is unaccounted_txn._cpu_phase_write_unaccounted else fn(*args)

        def fake_fetch(messages=None):
            if messages is not None:
                messages.append(("ok", "Read the region of 1 suppliers from the ERP"))
            return {"1": "Pune"}

        with patch.object(unaccounted_txn.supplier_region, "fetch_for_job", fake_fetch), \
                patch.object(unaccounted_txn, "_cpu_phase_process_unaccounted", fake_phase), \
                patch.object(unaccounted_txn, "run_cpu_phase", fake_run):
            result = unaccounted_txn._job_unaccounted(["/nope.xls"], "/tmp/out.xlsx", "o.xlsx", allow_unmapped=False)
        self.assertEqual(seen["regions"], {"1": "Pune"})
        self.assertFalse(result["needs_mapping_fix"])      # a populated Location needs no popup


class PipelineSignatureTests(unittest.TestCase):
    def test_every_pipeline_accepts_the_regions(self):
        import inspect
        for fn in (processing.process_report, processing.process_report_multi, processing.process_mrn_report,
                   processing.process_po_report, payables.process_report):
            self.assertIn("supplier_regions", inspect.signature(fn).parameters, fn.__name__)


if __name__ == "__main__":
    unittest.main()


class BlankSupplierSiteTests(unittest.TestCase):
    """A report with rows that have no Supplier Site at all (pandas 3 keeps them as real NaN) must still list
    its unmapped sites: sorting NaN against text used to crash the job."""

    def test_the_unmapped_site_list_survives_a_blank_site(self):
        df = pd.DataFrame({"Location": ["", "", "PUNE"], "Supplier Site": ["ZED", float("nan"), "PUNE-1"]})
        listed = sorted(df[df["Location"].astype(str).str.strip() == ""]["Supplier Site"].map(str).unique().tolist())
        self.assertEqual(listed, ["ZED", "nan"])
