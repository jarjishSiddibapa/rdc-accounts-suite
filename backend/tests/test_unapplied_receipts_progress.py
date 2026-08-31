"""Progress-reporting coverage for Unapplied Receipts' Excel writer and its
CPU-phase wiring in app.routers.unapplied_receipts - same gap class as the
ERP converter before its fix: the "Classifying, validating, and writing
the workbook..." phase (0.55 -> 1.0) previously reported nothing at all
for however long that took on a large file, since neither the writer nor
the CPU-phase wrapper function accepted a progress_cb.
"""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.routers.unapplied_receipts import _cpu_phase_finish_report
from app.services.unapplied_receipts import processor


def _fake_main_df(n=300):
    return pd.DataFrame({
        "Customer Number": [1000 + i for i in range(n)],
        "Due Days": [i % 100 for i in range(n)],
        "Unapplied Amount": [1000.0 + i for i in range(n)],
        "Location": [f"Plant {i % 5}" for i in range(n)],
        "Ageing Bucket": ["0 - 30"] * n,
    })


class WriteFormattedExcelProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_reports_moving_progress_scaled_into_its_span(self):
        df_main = _fake_main_df()
        df_advance = pd.DataFrame(columns=df_main.columns)
        events = []
        out = str(Path(self.tmp.name) / "out.xlsx")
        processor.write_formatted_excel(
            df_main, df_advance, out, dt.date(2026, 8, 31),
            incharge_map={f"Plant {i}": "Someone" for i in range(5)},
            progress_cb=lambda f, p: events.append((f, p)),
        )
        self.assertGreater(len(events), 2)
        fractions = [f for f, _ in events]
        self.assertEqual(fractions, sorted(fractions))
        self.assertGreaterEqual(fractions[0], 0.55)
        self.assertAlmostEqual(fractions[-1], 0.95, places=4)

    def test_without_progress_cb_is_unaffected(self):
        df_main = _fake_main_df(n=5)
        df_advance = pd.DataFrame(columns=df_main.columns)
        out = str(Path(self.tmp.name) / "out.xlsx")
        processor.write_formatted_excel(
            df_main, df_advance, out, dt.date(2026, 8, 31),
            incharge_map={f"Plant {i}": "Someone" for i in range(5)},
        )
        self.assertTrue(Path(out).is_file())


class CpuPhaseFinishReportWiringTests(unittest.TestCase):
    def test_forwards_progress_cb_to_the_writer(self):
        sentinel = object()
        with (
            patch(
                "app.routers.unapplied_receipts.processor.classify_advance_customers",
                return_value=("main_df", "advance_df", {}),
            ),
            patch(
                "app.routers.unapplied_receipts.processor._validate_before_save",
                return_value=[],
            ),
            patch("app.routers.unapplied_receipts.processor.write_formatted_excel") as write_mock,
        ):
            _cpu_phase_finish_report(
                "df", "ageing.xlsx", {}, {}, "out.xlsx", dt.date(2026, 8, 31), None,
                progress_cb=sentinel,
            )
        _, kwargs = write_mock.call_args
        self.assertIs(kwargs.get("progress_cb"), sentinel)


if __name__ == "__main__":
    unittest.main()
