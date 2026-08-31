"""Real, end-to-end verification that the Unaccounted/MRN/PO report writers
produce genuinely correct pivot data - both in the file's raw cell content
(what Excel's Protected View shows before any recalculation) and in the
mail-body HTML preview (mailer_shared.sheet_to_html), which reads that same
raw content directly and never opens the file in real Excel.

This is the coverage gap that let a real production incident through: the
suite's native-Excel-PivotTable feature (since reverted - see
excel_writers.py) only ever got its correct numbers from a live Excel
recalculation, so both of these read paths showed the pivot template's
one-row placeholder seed data forever. The existing test_desktop_parity.py
tests mock write_formatted_excel/write_formatted_mrn_excel entirely, so they
never exercised any of this."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app.services import mailer_shared
from app.services.unaccounted import excel_writers


class UnaccountedPivotDataIsRealTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name)

    def test_unaccounted_main_pivot_reflects_real_counts(self):
        df = pd.DataFrame({
            "Supplier Name": ["Acme Traders", "Bharat Steel", "Acme Traders", "Coastal Logistics"],
            "Supplier Site": ["SITE-A1", "SITE-B1", "SITE-A1", "SITE-C1"],
            "Invoice Number": ["INV-1001", "INV-1002", "INV-1003", "INV-1004"],
            "Invoice Date": ["01-Jul-2026", "05-Jul-2026", "10-Jul-2026", "15-Jul-2026"],
            "Amount": [125000.50, 88000.00, 42000.25, 61000.00],
            "GL Date": ["01-Jul-2026", "05-Jul-2026", "10-Jul-2026", "15-Jul-2026"],
            "Location": ["Mumbai Plant", "Chennai Plant", "Mumbai Plant", "Delhi Plant"],
            "Accounts Incharge": ["Rakesh D", "Priya S", "Rakesh D", "Anil K"],
        })
        out = str(self.out_dir / "unaccounted.xlsx")
        excel_writers.write_formatted_excel(df, out)

        html = mailer_shared.sheet_to_html(out, "Main")
        self.assertNotIn("SYNTHETIC", html)
        self.assertNotIn("Synthetic placeholder", html)
        self.assertNotIn("Jan-00", html)
        self.assertIn("Mumbai Plant", html)
        self.assertIn("Rakesh D", html)
        # Mumbai Plant / Rakesh D has 2 rows; Grand Total across all 4 rows is 4.
        self.assertIn(">2<", html)
        self.assertIn("Grand Total", html)
        self.assertIn(">4<", html)

    def test_mrn_locationwise_pivot_reflects_real_counts_and_caches_formula_values(self):
        df = pd.DataFrame({
            "SUPPLIER SITE": ["SITE-A1", "SITE-B1", "SITE-A1", "SITE-D1"],
            "SUPPLIER NAME": ["Acme Traders", "Bharat Steel", "Acme Traders", "Delta Fabricators"],
            "ACCOUNTING PERIOD": ["JUL-2026", "JUL-2026", "AUG-2026", "AUG-2026"],
            "Location": ["Mumbai Plant", "Chennai Plant", "Mumbai Plant", "Pune Plant"],
            "Accounts Incharge": ["Rakesh D", "Priya S", "Rakesh D", "Sneha R"],
            "MRN Number": ["MRN-1", "MRN-2", "MRN-3", "MRN-4"],
        })
        out = str(self.out_dir / "mrn.xlsx")
        excel_writers.write_formatted_mrn_excel(df, out)

        html = mailer_shared.sheet_to_html(out, "Locationwise Pivot")
        self.assertNotIn("SYNTHETIC", html)
        self.assertNotIn("Synthetic placeholder", html)
        self.assertIn("Mumbai Plant", html)
        self.assertIn(">2<", html)  # Mumbai Plant / Rakesh D: Jul-26 + Aug-26 = 2

        vendorwise_html = mailer_shared.sheet_to_html(out, "Vendorwise Pivot")
        self.assertNotIn("SYNTHETIC", vendorwise_html)
        self.assertNotIn("Synthetic placeholder", vendorwise_html)
        self.assertIn("Acme Traders", vendorwise_html)
        self.assertIn(">2<", vendorwise_html)  # Mumbai Plant / Acme Traders: Jul-26 + Aug-26 = 2

        # The raw saved file must carry a cached <v> value alongside every
        # formula, so Excel's Protected View (which does not auto-recalculate)
        # still shows the right numbers instead of blanks.
        import zipfile
        import re

        with zipfile.ZipFile(out) as zf:
            sheet_xml = None
            for name in zf.namelist():
                if name.startswith("xl/worksheets/sheet"):
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    if "Grand Total" in content and "SUBTOTAL" in content:
                        sheet_xml = content
                        break
            self.assertIsNotNone(sheet_xml, "Could not find the Locationwise Pivot sheet XML")
            formula_cells = re.findall(r"<f>[^<]+</f><v>[^<]+</v>", sheet_xml)
            self.assertGreater(len(formula_cells), 0)

    def test_po_main_pivot_reflects_real_counts(self):
        main_df = pd.DataFrame({
            "PO Number": ["PO-1", "PO-2", "PO-3", "PO-4"],
            "Supplier Site": ["SITE-A1", "SITE-B1", "SITE-A1", "SITE-E1"],
            "Location": ["Mumbai Plant", "Chennai Plant", "Mumbai Plant", "Kolkata Plant"],
            "Accounts Incharge": ["Rakesh D", "Priya S", "Rakesh D", "Divya M"],
            "Month": ["Jul-26", "Jul-26", "Aug-26", "Aug-26"],
            "Unit Price": [100.0, 200.0, 50.0, 75.0],
            "Quantity": [10, 5, 20, 8],
            "Basic Amount": [1000.0, 1000.0, 1000.0, 600.0],
            "PO Amount": [1180.0, 1180.0, 1180.0, 708.0],
            "GST Amount": [180.0, 180.0, 180.0, 108.0],
        })
        moved_df = pd.DataFrame(columns=main_df.columns)
        unmapped_df = pd.DataFrame(columns=["Supplier Site"])
        out = str(self.out_dir / "po.xlsx")
        excel_writers.write_formatted_po_excel(main_df, moved_df, unmapped_df, out)

        html = mailer_shared.sheet_to_html(out, "Main")
        self.assertNotIn("SYNTHETIC", html)
        self.assertNotIn("Synthetic placeholder", html)
        self.assertIn("Mumbai Plant", html)
        self.assertIn(">2<", html)  # Mumbai Plant / Rakesh D: 2 distinct POs


class WriterProgressReportingTests(unittest.TestCase):
    """These writers used to have no progress hook at all - a large file's
    entire write phase reported nothing until it finished, the same
    "frozen" symptom the ERP converter had before its own fix. progress_cb
    is optional everywhere (omitting it, as every other test in this file
    does, must keep working unchanged)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name)

    def _big_unaccounted_df(self, n=600):
        return pd.DataFrame({
            "Supplier Name": [f"Vendor {i % 20}" for i in range(n)],
            "Supplier Site": [f"SITE-{i % 20}" for i in range(n)],
            "Invoice Number": [f"INV-{i}" for i in range(n)],
            "Invoice Date": ["01-Jul-2026"] * n,
            "Amount": [1000.0 + i for i in range(n)],
            "GL Date": ["01-Jul-2026"] * n,
            "Location": [f"Plant {i % 5}" for i in range(n)],
            "Accounts Incharge": [f"Incharge {i % 5}" for i in range(n)],
        })

    def test_write_formatted_excel_reports_moving_progress(self):
        events = []
        out = str(self.out_dir / "out.xlsx")
        excel_writers.write_formatted_excel(
            self._big_unaccounted_df(), out, progress_cb=lambda f, p: events.append((f, p))
        )
        self.assertGreater(len(events), 2, "expected more than a start/end pair of updates")
        fractions = [f for f, _ in events]
        self.assertEqual(fractions, sorted(fractions))
        self.assertGreaterEqual(fractions[0], 0.5)
        self.assertAlmostEqual(fractions[-1], 0.95, places=4)

    def test_write_formatted_excel_without_progress_cb_is_unaffected(self):
        out = str(self.out_dir / "out.xlsx")
        excel_writers.write_formatted_excel(self._big_unaccounted_df(), out)
        self.assertTrue(Path(out).is_file())

    def test_write_formatted_mrn_excel_reports_moving_progress(self):
        n = 600
        df = pd.DataFrame({
            "ACCOUNTING PERIOD": ["Jul-26"] * n,
            "SUPPLIER SITE": [f"SITE-{i % 20}" for i in range(n)],
            "SUPPLIER NAME": [f"Vendor {i % 20}" for i in range(n)],
            "BASE AMOUNT": [1000.0 + i for i in range(n)],
            "Location": [f"Plant {i % 5}" for i in range(n)],
            "Accounts Incharge": [f"Incharge {i % 5}" for i in range(n)],
        })
        events = []
        out = str(self.out_dir / "mrn.xlsx")
        excel_writers.write_formatted_mrn_excel(df, out, progress_cb=lambda f, p: events.append((f, p)))
        self.assertGreater(len(events), 2)
        self.assertAlmostEqual(events[-1][0], 0.95, places=4)

    def test_write_formatted_po_excel_progress_covers_all_three_sheets(self):
        n = 300
        main_df = pd.DataFrame({
            "PO Number": [f"PO-{i}" for i in range(n)],
            "Location": [f"Plant {i % 5}" for i in range(n)],
            "Month": ["Jul-26"] * n,
            "Unit Price": [10.0] * n,
            "Quantity": [1] * n,
            "Basic Amount": [10.0] * n,
            "PO Amount": [11.8] * n,
            "GST Amount": [1.8] * n,
        })
        moved_df = main_df.iloc[:50].copy()
        unmapped_df = pd.DataFrame({"Supplier Site": ["UNMAPPED-1"] * 10})
        events = []
        out = str(self.out_dir / "po.xlsx")
        excel_writers.write_formatted_po_excel(
            main_df, moved_df, unmapped_df, out, progress_cb=lambda f, p: events.append((f, p))
        )
        self.assertGreater(len(events), 2)
        # Progress must reach the top of its span once every sheet (main +
        # moved + unmapped, all sharing one running row count) is written,
        # not restart/plateau partway through after only the first sheet.
        self.assertAlmostEqual(events[-1][0], 0.95, places=4)


if __name__ == "__main__":
    unittest.main()
