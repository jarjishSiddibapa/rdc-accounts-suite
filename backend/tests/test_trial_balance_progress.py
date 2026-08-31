"""Progress-reporting coverage for Trial Balance's to_excel_bytes - same
gap class as the ERP converter before its fix: the "Generating Excel
output..." phase (0.7 -> 1.0) ran entirely inside a CPU-pool subprocess
with no progress hook, so a large trial balance could sit apparently
frozen for that whole span.
"""
import unittest

import pandas as pd

from app.services.trial_balance import processor
from app.services.trial_balance.processor import DISPLAY_COLUMNS


def _fake_df(n=500):
    return pd.DataFrame(
        {name: [f"{name}-{i}" if name not in ("Beginning Balance", "Period Activity", "Ending Balance")
                else 100.0 + i for i in range(n)]
         for name in DISPLAY_COLUMNS}
    )[DISPLAY_COLUMNS]


class ToExcelBytesProgressTests(unittest.TestCase):
    def test_reports_moving_progress_scaled_into_its_span(self):
        events = []
        xlsx_bytes = processor.to_excel_bytes(_fake_df(), progress_cb=lambda f, p: events.append((f, p)))
        self.assertTrue(xlsx_bytes)
        self.assertGreater(len(events), 2)
        fractions = [f for f, _ in events]
        self.assertEqual(fractions, sorted(fractions))
        self.assertGreaterEqual(fractions[0], 0.7)
        self.assertAlmostEqual(fractions[-1], 0.95, places=4)

    def test_without_progress_cb_is_unaffected(self):
        xlsx_bytes = processor.to_excel_bytes(_fake_df(n=5))
        self.assertTrue(xlsx_bytes)


if __name__ == "__main__":
    unittest.main()
