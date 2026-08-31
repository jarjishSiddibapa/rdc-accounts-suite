"""End-to-end coverage for the ERP HTML/XLS -> Excel converter: datatype and
formatting fidelity on the real conversion path, plus the CPU-pool progress
relay added so large conversions report real, moving progress instead of
appearing frozen (see app.jobs.run_cpu_phase / app.routers.erp_converter).
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from app import jobs as jobs_module
from app.jobs import JobUserError
from app.routers.erp_converter import _job_convert
from app.services.erp_converter import converter, xlsx_writer
from app.services.erp_converter.errors import ConversionError

_SAMPLE_HTML = """<html><head><style type="text/css">
.c0 {font-family: 'Calibri';font-size: 14.0pt;font-weight: bold;}
.c2 {font-family: 'Calibri';font-size: 9.0pt;font-weight: bold;background-color: #D9E1F2;}
.c3 {font-family: 'Calibri';font-size: 9.0pt;}
</style></head><body>
<p class="c0">Vendor Ledger Report</p>
<table class="c3" cellspacing="0">
<tr><td class="c2" colspan="2">Ledger Code</td><td class="c2">Amount</td><td class="c2">Invoice Date</td><td class="c2">TDS Note</td></tr>
<tr><td class="c3">00123</td><td class="c3"></td><td class="c3">1,234.50</td><td class="c3">31-Aug-2026</td><td class="c3">Not a date</td></tr>
<tr><td class="c3">00456</td><td class="c3"></td><td class="c3">(500.00)</td><td class="c3">01-Jan-2026</td><td class="c3">Plain text</td></tr>
</table>
</body></html>"""


class ErpConverterFidelityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.in_path = Path(self.tmp.name) / "report.xls"
        self.out_path = Path(self.tmp.name) / "report.xlsx"
        self.in_path.write_text(_SAMPLE_HTML, encoding="utf-8")

    def test_html_export_is_detected_and_converted(self):
        kind = converter.convert_file(str(self.in_path), str(self.out_path))
        self.assertEqual(kind, "html")
        self.assertTrue(self.out_path.exists())

    # Row 1: title. Row 2: a blank spacer row before the table. Row 3:
    # header. Rows 4-5: data.

    def test_leading_zero_code_stays_text(self):
        converter.convert_file(str(self.in_path), str(self.out_path))
        wb = load_workbook(self.out_path)
        ws = wb["Report"]
        self.assertEqual(ws["A4"].value, "00123")
        self.assertIsInstance(ws["A4"].value, str)

    def test_amount_becomes_a_real_number_with_indian_grouping_format(self):
        converter.convert_file(str(self.in_path), str(self.out_path))
        wb = load_workbook(self.out_path)
        ws = wb["Report"]
        self.assertEqual(ws["C4"].value, 1234.5)
        self.assertIn("#", ws["C4"].number_format)

    def test_parenthesized_amount_becomes_negative_number(self):
        converter.convert_file(str(self.in_path), str(self.out_path))
        wb = load_workbook(self.out_path)
        ws = wb["Report"]
        self.assertEqual(ws["C5"].value, -500)

    def test_date_becomes_a_real_date_value(self):
        converter.convert_file(str(self.in_path), str(self.out_path))
        wb = load_workbook(self.out_path)
        ws = wb["Report"]
        self.assertEqual(ws["D4"].value.strftime("%Y-%m-%d"), "2026-08-31")

    def test_non_date_text_is_left_as_plain_text(self):
        converter.convert_file(str(self.in_path), str(self.out_path))
        wb = load_workbook(self.out_path)
        ws = wb["Report"]
        self.assertEqual(ws["E4"].value, "Not a date")

    def test_header_row_keeps_bold_font_and_fill(self):
        converter.convert_file(str(self.in_path), str(self.out_path))
        wb = load_workbook(self.out_path)
        ws = wb["Report"]
        header_cell = ws["A3"]
        self.assertTrue(header_cell.font.bold)
        self.assertIsNotNone(header_cell.fill.fgColor.rgb)

    def test_colspan_becomes_a_merged_range(self):
        converter.convert_file(str(self.in_path), str(self.out_path))
        wb = load_workbook(self.out_path)
        ws = wb["Report"]
        merged_ranges = {str(r) for r in ws.merged_cells.ranges}
        self.assertIn("A3:B3", merged_ranges)


class ExcelSheetLimitTests(unittest.TestCase):
    """write-only mode has no idea Excel caps a sheet at 1,048,576 rows /
    16,384 columns and will happily keep writing past that, producing a
    file Excel then refuses to open cleanly. XlsxWriter checks as it goes
    so this fails fast with an actionable message instead."""

    def test_row_limit_raises_a_clear_conversion_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "report.xls"
            in_path.write_text(_SAMPLE_HTML, encoding="utf-8")
            with patch.object(xlsx_writer, "_MAX_EXCEL_ROWS", 3):
                with self.assertRaises(ConversionError) as ctx:
                    converter.convert_file(str(in_path), str(Path(tmp) / "out.xlsx"))
            self.assertIn("exceeds Excel's per-sheet limit", str(ctx.exception))

    def test_job_convert_translates_conversion_error_into_job_user_error(self):
        with patch(
            "app.routers.erp_converter.jobs.run_cpu_phase",
            side_effect=ConversionError("This report converts to too many rows."),
        ):
            with self.assertRaises(JobUserError) as ctx:
                _job_convert("in.xls", "out.xlsx", "report.xls")
        self.assertIn("too many rows", str(ctx.exception))


def _toy_cpu_task(steps: int, progress_cb=None) -> str:
    """Module-level (picklable) stand-in for a real CPU-phase job body -
    reports several distinct progress updates like convert_file does, each
    spaced out enough for run_cpu_phase's 0.25s poll loop to observe more
    than just the final one."""
    import time

    for i in range(steps):
        if progress_cb:
            progress_cb((i + 1) / steps, f"step {i + 1}/{steps}")
        time.sleep(0.12)
    return "done"


class RunCpuPhaseProgressRelayTests(unittest.TestCase):
    """app.jobs.run_cpu_phase relays a CPU-pool worker's own progress_cb
    calls back to the caller via a cross-process queue - this is what makes
    large ERP conversions show real, moving progress instead of sitting at
    a single fixed percentage for the whole call (the originally reported
    "stuck at 2%" symptom)."""

    def test_progress_crosses_the_process_boundary(self):
        events = []
        result = jobs_module.run_cpu_phase(
            _toy_cpu_task, 8, progress_cb=lambda frac, phase: events.append((frac, phase))
        )
        self.assertEqual(result, "done")
        self.assertGreaterEqual(len(events), 2, "expected more than one distinct progress update")
        self.assertEqual(events[-1][0], 1.0)
        fractions = [e[0] for e in events]
        self.assertEqual(fractions, sorted(fractions))

    def test_omitting_progress_cb_is_unaffected(self):
        result = jobs_module.run_cpu_phase(_toy_cpu_task, 3)
        self.assertEqual(result, "done")


if __name__ == "__main__":
    unittest.main()
