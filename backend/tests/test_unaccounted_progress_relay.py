"""Confirms the CPU-phase wrapper functions in app.routers.unaccounted_txn
correctly forward progress_cb through to their writer calls - the same
wiring mistake (accepting a callback but never passing it on) that made the
ERP converter appear frozen at a fixed percentage for the whole run before
that was fixed. These are unit-level wiring checks; app.services.unaccounted.
excel_writers' own progress math is covered by tests/test_unaccounted_excel_
writers.py, and the underlying cross-process relay mechanism itself by
tests/test_erp_converter.py.

Also confirms the process/write split used for the pre-flight mapping check
(app.routers.unaccounted_txn._job_unaccounted/_job_mrn/_job_po): the writer
phase must not run when the process phase finds unmapped sites.
"""
import unittest
from unittest.mock import patch

from app.routers import unaccounted_txn


class CpuPhaseProgressWiringTests(unittest.TestCase):
    def test_unaccounted_write_phase_forwards_progress_cb_to_the_writer(self):
        sentinel = object()
        with patch("app.routers.unaccounted_txn.excel_writers.write_formatted_excel") as write_mock:
            unaccounted_txn._cpu_phase_write_unaccounted("df", "out.xlsx", progress_cb=sentinel)
        write_mock.assert_called_once_with("df", "out.xlsx", progress_cb=sentinel)

    def test_mrn_write_phase_forwards_progress_cb_to_the_writer(self):
        sentinel = object()
        with patch("app.routers.unaccounted_txn.excel_writers.write_formatted_mrn_excel") as write_mock:
            unaccounted_txn._cpu_phase_write_mrn("df", "out.xlsx", progress_cb=sentinel)
        write_mock.assert_called_once_with("df", "out.xlsx", progress_cb=sentinel)

    def test_po_write_phase_forwards_progress_cb_to_the_writer(self):
        sentinel = object()
        with patch("app.routers.unaccounted_txn.excel_writers.write_formatted_po_excel") as write_mock:
            unaccounted_txn._cpu_phase_write_po("main", "moved", "unmapped", "out.xlsx", progress_cb=sentinel)
        write_mock.assert_called_once_with("main", "moved", "unmapped", "out.xlsx", progress_cb=sentinel)


class ProcessThenWriteSplitTests(unittest.TestCase):
    def test_job_unaccounted_passes_its_progress_cb_into_the_write_phase(self):
        import pandas as pd

        write_call_kwargs = {}

        def fake_run_cpu_phase(fn, *args, **kwargs):
            if fn is unaccounted_txn._cpu_phase_process_unaccounted:
                df = pd.DataFrame({"Location": ["X"], "Supplier Site": ["A"]})
                return (df, 1, 1, 1, [])
            write_call_kwargs.update(kwargs)
            return None

        my_cb = lambda frac, phase: None  # noqa: E731
        with (
            patch("app.routers.unaccounted_txn.run_cpu_phase", side_effect=fake_run_cpu_phase),
            patch("app.routers.unaccounted_txn.Path.unlink"),
        ):
            result = unaccounted_txn._job_unaccounted(["a.xls"], "out.xlsx", "Report.xlsx", progress_cb=my_cb)

        self.assertIs(write_call_kwargs.get("progress_cb"), my_cb)
        self.assertFalse(result["needs_mapping_fix"])
        self.assertEqual(result["output_path"], "out.xlsx")

    def test_job_unaccounted_skips_the_write_phase_when_sites_are_unmapped(self):
        import pandas as pd

        write_calls = []

        def fake_run_cpu_phase(fn, *args, **kwargs):
            if fn is unaccounted_txn._cpu_phase_process_unaccounted:
                df = pd.DataFrame({"Location": [""], "Supplier Site": ["A"]})
                return (df, 1, 1, 0, [])
            write_calls.append((fn, args, kwargs))
            return None

        with (
            patch("app.routers.unaccounted_txn.run_cpu_phase", side_effect=fake_run_cpu_phase),
            patch("app.routers.unaccounted_txn.Path.unlink"),
        ):
            result = unaccounted_txn._job_unaccounted(["a.xls"], "out.xlsx", "Report.xlsx")

        self.assertEqual(write_calls, [])
        self.assertTrue(result["needs_mapping_fix"])
        self.assertEqual(result["unmapped_sites"], ["A"])
        self.assertNotIn("output_path", result)


if __name__ == "__main__":
    unittest.main()
