"""Progress-reporting coverage for RDC Payables' to_excel_bytes. Unlike
the other report writers, this one runs on the calling thread rather than
the CPU process pool (RDC Payables already has its own separate,
RAM-budgeted ProcessPoolExecutor for HTML parsing - see processor.py's
_get_pool), so progress_cb is called directly with no cross-process
relay involved. It previously had no progress hook at all, so the
"Generating Excel output..." phase (0.86 -> 1.0) reported nothing until
the whole call returned on a large report.
"""
import unittest

import pandas as pd

from app.services.rdc_payables import processor


def _fake_df(n=400):
    return pd.DataFrame({
        "Region": [f"Region {i % 5}" for i in range(n)],
        "Accounts Incharge": [f"Incharge {i % 5}" for i in range(n)],
        "Transaction Type": ["IOCL"] * n,
        "Aging Bucket": ["Less than or equal to 30 Days"] * n,
        "Accounted Outstanding Amount": [1000.0 + i for i in range(n)],
    })


class ToExcelBytesProgressTests(unittest.TestCase):
    def test_reports_moving_progress_scaled_into_its_span(self):
        events = []
        xlsx_bytes = processor.to_excel_bytes(_fake_df(), progress_cb=lambda f, p: events.append((f, p)))
        self.assertTrue(xlsx_bytes)
        self.assertGreater(len(events), 2)
        fractions = [f for f, _ in events]
        self.assertEqual(fractions, sorted(fractions))
        self.assertGreaterEqual(fractions[0], 0.86)
        self.assertAlmostEqual(fractions[-1], 0.99, places=4)

    def test_without_progress_cb_is_unaffected(self):
        xlsx_bytes = processor.to_excel_bytes(_fake_df(n=5))
        self.assertTrue(xlsx_bytes)


if __name__ == "__main__":
    unittest.main()
