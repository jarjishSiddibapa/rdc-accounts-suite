"""The input readers of Trial Balance and the GST adder run (both once crashed with a NameError
because the shared input helper was never imported)."""

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app.services.gst_invoice_adder import processor as gst
from app.services.trial_balance import processor as trial_balance


class ReportInputReaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_trial_balance_reads_an_html_report(self):
        path = self.tmp / "tb.xls"
        path.write_text("<html><body><table><tr><td>Account</td></tr></table></body></html>", encoding="utf-8")
        self.assertIn("Account", trial_balance._read_report_text(str(path)))

    def test_gst_adder_finds_the_header_row_and_reads_the_data(self):
        path = self.tmp / "ageing.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Aging report"])
        sheet.append([gst.COL_INV_NO, gst.COL_INV_DATE, "Customer Name"])
        sheet.append(["INV1", "2026-09-01", "Acme"])
        workbook.save(path)
        self.assertEqual(gst.detect_header_index(str(path)), 1)
        frame, _inv_no, _inv_date = gst.read_data_df(str(path))
        self.assertEqual(list(frame["Customer Name"]), ["Acme"])


if __name__ == "__main__":
    unittest.main()
