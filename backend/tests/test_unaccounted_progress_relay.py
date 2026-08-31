"""Confirms the CPU-phase wrapper functions in app.routers.unaccounted_txn
correctly forward progress_cb through to their processing + writer calls -
the same wiring mistake (accepting a callback but never passing it on)
that made the ERP converter appear frozen at a fixed percentage for the
whole run before that was fixed. These are unit-level wiring checks;
app.services.unaccounted.excel_writers' own progress math is covered by
tests/test_unaccounted_excel_writers.py, and the underlying cross-process
relay mechanism itself by tests/test_erp_converter.py.
"""
import unittest
from unittest.mock import patch

from app.routers import unaccounted_txn


class CpuPhaseProgressWiringTests(unittest.TestCase):
    def test_unaccounted_phase_forwards_progress_cb_to_the_writer(self):
        sentinel = object()
        with (
            patch(
                "app.routers.unaccounted_txn.processing.process_report_multi",
                return_value=("df", 1, 1, 1),
            ),
            patch("app.routers.unaccounted_txn.excel_writers.write_formatted_excel") as write_mock,
        ):
            unaccounted_txn._cpu_phase_unaccounted(["a.xls"], "out.xlsx", progress_cb=sentinel)
        write_mock.assert_called_once_with("df", "out.xlsx", progress_cb=sentinel)

    def test_mrn_phase_forwards_progress_cb_to_the_writer(self):
        sentinel = object()
        with (
            patch(
                "app.routers.unaccounted_txn.processing.process_mrn_report",
                return_value=("df", 1, 1, 1),
            ),
            patch("app.routers.unaccounted_txn.excel_writers.write_formatted_mrn_excel") as write_mock,
        ):
            unaccounted_txn._cpu_phase_mrn("a.xls", set(), "out.xlsx", progress_cb=sentinel)
        write_mock.assert_called_once_with("df", "out.xlsx", progress_cb=sentinel)

    def test_po_phase_forwards_progress_cb_to_the_writer(self):
        sentinel = object()
        with (
            patch(
                "app.routers.unaccounted_txn.processing.process_po_report",
                return_value=("main", "moved", "unmapped", 1, 1, 1),
            ),
            patch("app.routers.unaccounted_txn.excel_writers.write_formatted_po_excel") as write_mock,
        ):
            unaccounted_txn._cpu_phase_po("a.xls", set(), [], 0.82, "out.xlsx", progress_cb=sentinel)
        write_mock.assert_called_once_with("main", "moved", "unmapped", "out.xlsx", progress_cb=sentinel)

    def test_job_unaccounted_passes_its_progress_cb_into_run_cpu_phase(self):
        import pandas as pd

        recorded_kwargs = {}
        my_cb = lambda frac, phase: None  # noqa: E731

        def fake_run_cpu_phase(fn, *args, **kwargs):
            recorded_kwargs.update(kwargs)
            df = pd.DataFrame({"Location": [], "Supplier Site": []})
            return (df, 0, 0, 0, [])

        with (
            patch("app.routers.unaccounted_txn.run_cpu_phase", side_effect=fake_run_cpu_phase),
            patch("app.routers.unaccounted_txn.Path.unlink"),
        ):
            unaccounted_txn._job_unaccounted(["a.xls"], "out.xlsx", "Report.xlsx", progress_cb=my_cb)

        self.assertIs(recorded_kwargs.get("progress_cb"), my_cb)


if __name__ == "__main__":
    unittest.main()
