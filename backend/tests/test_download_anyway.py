"""Every report that waits for missing mappings can also be written anyway.

Each job below is driven twice with an unmapped value: normally it stops and
returns what has to be fixed without writing a file; with allow_unmapped it
writes the file and says it did so. The heavy parsing steps are replaced so
only the new branch is exercised.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from app.routers import rdc_payables, trial_balance, unaccounted_txn, unapplied_receipts, untagged_invoices_report


def _direct(fn, *args, **kwargs):
    kwargs.pop("progress_cb", None)
    return fn(*args, **kwargs)


class _TempDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _input(self, name="input.html"):
        path = self.dir / name
        path.write_text("x")
        return str(path)


class RdcPayablesTests(_TempDirCase):
    def _run(self, allow):
        df = pd.DataFrame({
            "Region": ["North", ""], "Vendor Site Code": ["S0", "S1"],
            "Transaction Type": ["Invoice", "Invoice"], "Aging Bucket": ["0-30", "0-30"],
        })
        output = str(self.dir / "out.xlsx")
        with patch.object(rdc_payables, "SessionLocal", MagicMock()), \
                patch.object(rdc_payables.mapping_store, "load_all", return_value=(None,) * 6), \
                patch.object(rdc_payables.processor, "parse_html_report", return_value=(["c"], [[1], [2]])), \
                patch.object(rdc_payables.processor, "process_report", return_value=(df, None)), \
                patch.object(rdc_payables.processor, "to_excel_bytes", return_value=b"xlsx"):
            result = rdc_payables._run_process_job(
                self._input(), output, 2026, 9, "r.xlsx", allow_unmapped=allow)
        return result, Path(output)

    def test_stops_by_default_and_writes_when_allowed(self):
        stopped, output = self._run(False)
        self.assertTrue(stopped["needs_mapping_fix"])
        self.assertFalse(output.exists())
        written, output = self._run(True)
        self.assertFalse(written["needs_mapping_fix"])
        self.assertTrue(written["allowed_unmapped"])
        self.assertEqual(written["unmapped_vendor_sites"], ["S1"])
        self.assertTrue(output.exists())


class TrialBalanceTests(_TempDirCase):
    def _run(self, allow):
        df = pd.DataFrame({"Region": ["North", ""]})
        output = str(self.dir / "out.xlsx")
        with patch.object(trial_balance, "SessionLocal", MagicMock()), \
                patch.object(trial_balance.mapping_store, "load_all", return_value=(None,) * 4), \
                patch.object(trial_balance, "run_cpu_phase", _direct), \
                patch.object(trial_balance.processor, "process_report", return_value=(df, {"L1"}, {"A1"})), \
                patch.object(trial_balance.processor, "to_excel_bytes", return_value=b"xlsx"):
            result = trial_balance._run_process_job(
                self._input(), ["c"], [[1], [2]], [], output, "r.xlsx", allow_unmapped=allow)
        return result, Path(output)

    def test_stops_by_default_and_writes_when_allowed(self):
        stopped, output = self._run(False)
        self.assertTrue(stopped["needs_mapping_fix"])
        self.assertFalse(output.exists())
        written, output = self._run(True)
        self.assertFalse(written["needs_mapping_fix"])
        self.assertTrue(written["allowed_unmapped"])
        self.assertEqual((written["missing_codes"], written["missing_account_ho"]), (["L1"], ["A1"]))
        self.assertTrue(output.exists())


class UnaccountedTests(_TempDirCase):
    def _writer(self, df, output, progress_cb=None, *rest):
        Path(output if isinstance(output, str) else rest[-1]).write_bytes(b"xlsx")

    def test_unaccounted_report(self):
        df = pd.DataFrame({"Location": ["Nagpur", ""], "Supplier Site": ["S0", "S1"]})
        for allow in (False, True):
            output = self.dir / f"ua{allow}.xlsx"
            with patch.object(unaccounted_txn, "run_cpu_phase", _direct), \
                    patch.object(unaccounted_txn, "_cpu_phase_process_unaccounted",
                                 return_value=(df, 2, 5, 1, [])), \
                    patch.object(unaccounted_txn, "_cpu_phase_write_unaccounted", self._writer):
                result = unaccounted_txn._job_unaccounted(
                    [self._input()], str(output), "n.xlsx", allow_unmapped=allow)
            self.assertEqual(result["needs_mapping_fix"], not allow)
            self.assertEqual(output.exists(), allow)
            self.assertEqual(result["unmapped_sites"], ["S1"])
            if allow:
                self.assertTrue(result["allowed_unmapped"])

    def test_mrn_report(self):
        from app.services.unaccounted.constants import MRN_SITE_COL
        df = pd.DataFrame({"Location": ["Nagpur", ""], MRN_SITE_COL: ["S0", "S1"]})
        for allow in (False, True):
            output = self.dir / f"mrn{allow}.xlsx"
            with patch.object(unaccounted_txn, "run_cpu_phase", _direct), \
                    patch.object(unaccounted_txn, "_cpu_phase_process_mrn", return_value=(df, 2, 5, 1, [])), \
                    patch.object(unaccounted_txn, "_cpu_phase_write_mrn", self._writer):
                result = unaccounted_txn._job_mrn(
                    self._input(), set(), str(output), "n.xlsx", allow_unmapped=allow)
            self.assertEqual(result["needs_mapping_fix"], not allow)
            self.assertEqual(output.exists(), allow)
            self.assertEqual(result["unmapped_sites"], ["S1"])

    def test_po_report(self):
        from app.services.unaccounted.constants import PO_SITE_COL
        main, moved = pd.DataFrame({"a": [1]}), pd.DataFrame({"a": []})
        unmapped = pd.DataFrame({PO_SITE_COL: ["S1"]})
        for allow in (False, True):
            output = self.dir / f"po{allow}.xlsx"
            with patch.object(unaccounted_txn, "run_cpu_phase", _direct), \
                    patch.object(unaccounted_txn, "_cpu_phase_process_po",
                                 return_value=(main, moved, unmapped, 2, 5, 1, [])), \
                    patch.object(unaccounted_txn, "_cpu_phase_write_po", self._po_writer):
                result = unaccounted_txn._job_po(
                    self._input(), set(), [], 0.8, str(output), "n.xlsx", allow_unmapped=allow)
            self.assertEqual(result["needs_mapping_fix"], not allow)
            self.assertEqual(output.exists(), allow)
            self.assertEqual(result["unmapped_sites"], ["S1"])

    @staticmethod
    def _po_writer(main_df, moved_df, unmapped_df, output_path, progress_cb=None):
        Path(output_path).write_bytes(b"xlsx")


class UnappliedReceiptsTests(_TempDirCase):
    def test_stops_by_default_and_writes_when_allowed(self):
        df = pd.DataFrame({"a": [1]})
        warnings = [{"category": "Accounts Incharge Not Mapped", "items": ["Nagpur"]}]

        def write(df_main, df_advance, output_path, *rest, **kwargs):
            Path(output_path).write_bytes(b"xlsx")
            return []

        for allow in (False, True):
            output = self.dir / f"ur{allow}.xlsx"
            with patch.object(unapplied_receipts, "SessionLocal", MagicMock()), \
                    patch.object(unapplied_receipts.mapping_store, "load_all", return_value=({}, {})), \
                    patch.object(unapplied_receipts, "run_cpu_phase", _direct), \
                    patch.object(unapplied_receipts.processor, "process_report",
                                 return_value=(df, 1, 0, pd.DataFrame(), True)), \
                    patch.object(unapplied_receipts, "_cpu_phase_classify_and_validate",
                                 return_value=(df, df, warnings, [])), \
                    patch.object(unapplied_receipts, "_cpu_phase_write_report", write):
                result = unapplied_receipts._run_process_job(
                    self._input(), self._input("ageing.xlsx"), str(output), "2026-09-30", allow_unmapped=allow)
            self.assertEqual(result["needs_mapping_fix"], not allow)
            self.assertEqual(output.exists(), allow)
            self.assertEqual(result["validation_warnings"], warnings)


class UntaggedInvoicesTests(_TempDirCase):
    def test_stops_by_default_and_writes_when_allowed(self):
        proc = untagged_invoices_report.processor
        df = pd.DataFrame({proc.LOCATION_COL: ["Nagpur", "Wada"]})

        def write(*args, **kwargs):
            Path(args[5]).write_bytes(b"xlsx")

        for allow in (False, True):
            output = self.dir / f"ti{allow}.xlsx"
            with patch.object(untagged_invoices_report, "SessionLocal", MagicMock()), \
                    patch.object(untagged_invoices_report.mapping_store, "load_all", return_value=({}, {})), \
                    patch.multiple(
                        proc,
                        fetch_ageing_from_erp=MagicMock(return_value=df),
                        missing_location_mappings=MagicMock(return_value=["RAW LOCATION"]),
                        add_location_column=MagicMock(return_value=df),
                        missing_incharge_mappings=MagicMock(return_value=[]),
                        recompute_ageing_buckets=MagicMock(return_value=df),
                        build_untagged_ageing=MagicMock(return_value=df),
                        build_untagged_summary=MagicMock(return_value=df),
                        build_below_1k_ageing=MagicMock(return_value=df),
                        build_below_1k_summary=MagicMock(return_value=df),
                        write_report=write,
                    ):
                result = untagged_invoices_report._run_process_job(str(output), allow_unmapped=allow)
            self.assertEqual(result["needs_mapping_fix"], not allow)
            self.assertEqual(output.exists(), allow)
            self.assertEqual(result["validation_warnings"][0]["items"], ["RAW LOCATION"])


if __name__ == "__main__":
    unittest.main()
