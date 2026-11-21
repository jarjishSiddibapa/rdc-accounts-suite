"""Every ageing carries the GST invoice number: the AR Ageing, and the Ageing / Untagged Ageing / Below 1k Ageing
sheets of the Untagged Invoices report (standalone and inside the Debtors mail), all from one GST look-up."""

import datetime as dt
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pandas as pd

from app.services.debtors_mailer import auto_mail as debtors
from app.services.erp_ageing import erp_frame
from app.services.gst_invoice_adder import processor as gst_processor
from app.services.untagged_invoices import processor, report_job

_COLUMNS = [
    "CUSTOMER_NAME", "CUSTOMER_ACCOUNT", "LOCATION", "TRX_NUMBER", "INVOICE_RCPT_CLASS",
    "CREDITLIMIT", "PAYMENT_TERM", "OUTSTANDING_AMOUNT", "DUE_DATE", "OVER_DUE_DAYS", "SALESPERSON",
]
_DAY = dt.datetime(2026, 1, 1)
_ROWS = [
    ("Acme", "1001", "Pune", "INV1", "Invoice", None, 30, 500, _DAY, 10, "Ravi"),          # below 1k
    ("Acme", "1001", "Pune", "CM1", "Credit Memo", None, 30, -250.5, _DAY, 10, None),     # untagged
    ("Acme", "1001", "Pune", "RCT1", "Receipts", None, None, -90, _DAY, 10, None),        # a receipt: no GST number
]
_GST = {("INV1", "01-Jan-26"): "GST/001", ("CM1", "01-Jan-26"): "GST/002"}


def _ageing():
    return erp_frame(_COLUMNS, _ROWS, include_buckets=False)


class AddGstColumnTests(unittest.TestCase):
    def test_it_goes_right_before_the_invoice_number_with_the_number_of_each_row(self):
        df = _ageing()
        out = processor.add_gst_column(df, _GST)
        columns = list(out.columns)
        self.assertEqual(columns[columns.index("Invoice No/ Receipt No") - 1], processor.GST_COL)
        self.assertEqual(out[processor.GST_COL].tolist(), ["GST/001", "GST/002", ""])      # blank where the ERP has none
        self.assertNotIn(processor.GST_COL, df.columns)                                    # the caller's frame is untouched

    def test_a_row_with_no_invoice_number_is_kept_with_a_blank_number(self):
        df = _ageing()
        df.loc[0, "Invoice No/ Receipt No"] = None
        out = processor.add_gst_column(df, _GST)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[processor.GST_COL].tolist(), ["", "GST/002", ""])

    def test_the_key_is_the_one_the_look_up_used(self):
        df = _ageing()
        pairs = gst_processor._extract_pairs_vectorized(df, gst_processor.COL_INV_NO, gst_processor.COL_INV_DATE)
        found = {pair: f"G-{pair[0]}" for pair in pairs}
        self.assertEqual(gst_processor.gst_for_rows(df, found), ["G-INV1", "G-CM1", "G-RCT1"])

    def test_it_is_a_text_column_not_an_amount(self):
        self.assertFalse(processor._is_numeric_col(processor.GST_COL))


class ReportCarriesItOnEveryAgeingSheetTests(unittest.TestCase):
    def _build(self, **kw):
        out = Path(tempfile.mkdtemp()) / "untagged.xlsx"
        built = report_job.build_report(
            str(out), maps=({"PUNE": "Pune"}, {"Pune": "Ravi"}), ageing=_ageing, regions={},
            as_on_date=dt.date(2026, 1, 31), **kw)
        return built, openpyxl.load_workbook(out)

    def test_the_ageing_untagged_ageing_and_below_1k_ageing_sheets_all_have_it_before_the_invoice_number(self):
        built, wb = self._build(gst=_GST)
        self.assertEqual(built.result["gst_found"], 2)
        expected = {"Ageing": ["GST/001", "GST/002", "-"], "Untagged Ageing": ["GST/002"], "Below 1k Ageing": ["GST/001"]}
        for title, numbers in expected.items():
            header = [c.value for c in wb[title][1]]
            at = header.index(processor.GST_COL)
            self.assertEqual(header[at + 1], "Invoice No/ Receipt No", title)
            self.assertEqual([row[at].value for row in wb[title].iter_rows(min_row=2)], numbers, title)

    def test_the_invoice_date_is_still_a_real_excel_date(self):
        _, wb = self._build(gst=_GST)
        ws = wb["Ageing"]
        header = [c.value for c in ws[1]]
        cell = ws.cell(row=2, column=header.index("Invoice/ Receipt Date") + 1)
        self.assertIsInstance(cell.value, dt.datetime)
        self.assertIn("MMM", cell.number_format.upper())

    def test_a_callable_gst_source_is_used_and_nothing_is_fetched(self):
        with patch.object(report_job.gst_job, "gst_numbers_of", side_effect=AssertionError("fetched")):
            built, wb = self._build(gst=lambda: _GST)
        self.assertEqual(built.result["gst_found"], 2)

    def test_without_one_the_numbers_are_looked_up_for_the_ageing_in_hand(self):
        with patch.object(report_job.gst_job, "gst_numbers_of", return_value=_GST) as lookup:
            built, _ = self._build()
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(built.result["gst_found"], 2)

    def test_a_report_held_for_missing_mappings_does_not_look_up_numbers(self):
        out = Path(tempfile.mkdtemp()) / "held.xlsx"
        with patch.object(report_job.gst_job, "gst_numbers_of", side_effect=AssertionError("fetched")):
            built = report_job.build_report(
                str(out), maps=({}, {}), ageing=_ageing, regions={}, as_on_date=dt.date(2026, 1, 31))
        self.assertTrue(built.result["needs_mapping_fix"])
        self.assertFalse(out.exists())

    def test_the_debtors_incharge_column_still_follows_location(self):
        _, wb = self._build(gst=_GST, incharge_column=True)
        header = [c.value for c in wb["Ageing"][1]]
        self.assertEqual(header[header.index("Location") + 1], "Accounts Incharge")
        self.assertIn(processor.GST_COL, header)


class OneLookUpForEveryReportTests(unittest.TestCase):
    def test_the_reports_asking_at_once_share_one_look_up(self):
        calls = []
        started = threading.Event()

        def lookup(df, progress_cb=None, cancel_event=None, log_q=None):
            calls.append(1)
            started.wait(0.2)
            return dict(_GST)

        shared = debtors._SharedGst(_ageing, threading.Event())
        results = []
        with patch.object(debtors.gst_job, "gst_numbers_of", side_effect=lookup):
            threads = [threading.Thread(target=lambda: results.append(shared.get())) for _ in range(3)]
            for thread in threads:
                thread.start()
            started.set()
            for thread in threads:
                thread.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [_GST, _GST, _GST])

    def test_the_ageing_and_the_untagged_report_are_handed_the_same_look_up(self):
        seen = {}

        def gst(path, name, **kw):
            seen["ageing"] = kw["gst_map"]
            kw["ageing"]()
            return type("R", (), {"result": {"total": 1, "found": 1, "blank": 0}, "df": _ageing(), "gst_map": {}})()

        def untagged(path, **kw):
            seen["untagged"] = kw["gst"]
            return type("R", (), {"result": {"needs_mapping_fix": True, "validation_warnings": []}})()

        def unapplied(path, *a, **kw):
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
                patch.object(debtors.erp_ageing, "oracle_config", lambda: object()), \
                patch.object(debtors.erp_ageing, "fetch_ageing_from_erp", lambda *a, **k: _ageing()), \
                patch.object(debtors, "fetch_register_from_erp", lambda *a, **k: None), \
                patch.object(debtors.customer_region, "RegionFetch", FakeRegions), \
                patch.object(debtors.gst_job, "build_report", gst), \
                patch.object(debtors.untagged_job, "build_report", untagged), \
                patch.object(debtors.unapplied_job, "build_report", unapplied):
            debtors.build({"send_when_unmapped": False})
        self.assertEqual(seen["ageing"], seen["untagged"])          # one bound method of one shared look-up
        self.assertIs(seen["ageing"].__self__, seen["untagged"].__self__)


if __name__ == "__main__":
    unittest.main()
