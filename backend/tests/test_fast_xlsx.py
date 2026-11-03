"""The fast row writer must write exactly what xlsxwriter writes - every part of the file."""

import datetime as dt
import math
import os
import random
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import xlsxwriter
from openpyxl import load_workbook

from app.services import fast_xlsx

_OPTIONS = {"constant_memory": True, "default_date_format": "dd-mmm-yy", "strings_to_formulas": False,
            "strings_to_numbers": False, "strings_to_urls": False, "nan_inf_to_errors": True}


def _parts(path):
    with zipfile.ZipFile(path) as z:
        # the creation time in docProps/core.xml is the only thing that differs between two runs
        return {name: z.read(name) for name in z.namelist() if name != "docProps/core.xml"}


def _write(path, sheet_class, build):
    wb = xlsxwriter.Workbook(path, dict(_OPTIONS))
    sheets = build(wb, sheet_class)
    fast_xlsx.close_workbook(wb, sheets)


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def same(self, build):
        slow, fast = str(self.dir / "slow.xlsx"), str(self.dir / "fast.xlsx")
        _write(slow, fast_xlsx.ClassicSheet, build)
        _write(fast, fast_xlsx.FastSheet, build)
        a, b = _parts(slow), _parts(fast)
        self.assertEqual(sorted(a), sorted(b))
        for name in a:
            self.assertEqual(a[name], b[name], name)
        return fast


def _formats(wb):
    return {
        "head": wb.add_format({"bold": True, "bg_color": "#C2D8ED", "border": 1}),
        "text": wb.add_format({"font_name": "Calibri", "border": 1}),
        "alt": wb.add_format({"font_name": "Calibri", "border": 1, "bg_color": "#EDF2FB"}),
        "dec": wb.add_format({"num_format": "#,##0.00", "border": 1}),
        "int": wb.add_format({"num_format": "0", "border": 1, "align": "center"}),
        "day": wb.add_format({"num_format": "DD-MMM-YYYY", "border": 1}),
    }


class SameAsXlsxwriterTests(_Case):
    def test_the_self_check_sample_matches(self):
        slow = fast_xlsx._sample(str(self.dir / "a.xlsx"), fast_xlsx.ClassicSheet)
        fast = fast_xlsx._sample(str(self.dir / "b.xlsx"), fast_xlsx.FastSheet)
        # core.xml holds the creation time
        for name in slow:
            if name != "docProps/core.xml":
                self.assertEqual(slow[name], fast[name], name)

    def test_random_rows_of_every_kind_of_value(self):
        rng = random.Random(7)
        texts = ["", " ", "plain", " lead", "trail ", "a&b", "<x>", "q\"uote'", "x\ny", "tab\tx", "_x0041_",
                 "\x00\x0b\x1f", "unicode é ₹ 漢字", "line\r\nbreak", "<r>rich</r>", "x" * 40000, "￾"]
        days = [dt.datetime(2026, 1, 1), dt.datetime(2026, 11, 3, 14, 30), dt.date(2026, 5, 5), dt.time(9, 30),
                dt.timedelta(hours=5)]
        numbers = [0, 1, -1, 12345678901234, 0.1, -2.5e-9, 1e300, math.nan, math.inf, -math.inf, Decimal("3.14")]

        def build(wb, sheet_class):
            f = _formats(wb)
            ws = wb.add_worksheet("Data")
            ws.write_row(0, 0, ["a", "b", "c", "d", "e", "f"], f["head"])
            ws.set_column(0, 5, 14)
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, 400, 5)
            sheet = sheet_class(ws)
            rows = random.Random(11)
            for r in range(1, 401):
                sheet.begin_row(r)
                for ci in range(6):
                    kind = rows.choice(("t", "n", "d", "b", "z", "a", "k"))
                    fmt = rows.choice([f["text"], f["alt"], f["dec"], f["int"], f["day"], None])
                    if kind == "t":
                        sheet.text(ci, rows.choice(texts), fmt)
                    elif kind == "n":
                        sheet.number(ci, rows.choice(numbers), fmt)
                    elif kind == "d":
                        sheet.date(ci, rows.choice(days), fmt)
                    elif kind == "b":
                        sheet.boolean(ci, rows.choice((True, False)), fmt)
                    elif kind == "z":
                        sheet.blank(ci, fmt)
                    elif kind == "a":
                        sheet.auto(ci, rows.choice(texts + numbers + days + [None, True, False]), fmt)
                    # "k": a gap: the cell is left out
                sheet.end_row()
            return [sheet]

        self.same(build)

    def test_gaps_and_skipped_rows_and_dimensions(self):
        def build(wb, sheet_class):
            f = _formats(wb)
            ws = wb.add_worksheet("Gaps")
            ws.write_row(0, 0, ["a", "b", "c", "d"], f["head"])
            sheet = sheet_class(ws)
            sheet.begin_row(1)
            sheet.text(2, "only the third", f["text"])
            sheet.end_row()
            sheet.begin_row(2)                         # a row with no cells at all: nothing is written
            sheet.end_row()
            sheet.begin_row(3)
            sheet.auto(0, None)
            sheet.auto(1, "")
            sheet.end_row()
            sheet.begin_row(9)                         # and a jump
            sheet.number(3, 5, f["dec"])
            sheet.end_row()
            return [sheet]

        self.same(build)

    def test_no_data_rows_at_all(self):
        def build(wb, sheet_class):
            f = _formats(wb)
            ws = wb.add_worksheet("Empty")
            ws.write_row(0, 0, ["a", "b"], f["head"])
            ws.autofilter(0, 0, 0, 1)
            return [sheet_class(ws)]

        self.same(build)

    def test_several_sheets_with_shared_formats_and_ordinary_sheets_between(self):
        def build(wb, sheet_class):
            f = _formats(wb)
            sheets = []
            first = wb.add_worksheet("One")
            first.write_row(0, 0, ["a", "b"], f["head"])
            sheet = sheet_class(first)
            for r in range(1, 30):
                sheet.begin_row(r)
                sheet.text(0, f"row {r}", f["alt"] if r % 2 else f["text"])
                sheet.number(1, r * 1.5, f["dec"])
                sheet.end_row()
            sheets.append(sheet)
            plain = wb.add_worksheet("Summary")          # written by xlsxwriter itself, using the same formats
            plain.write_row(0, 0, ["x", "y"], f["head"])
            plain.write_number(1, 0, 3, f["dec"])
            second = wb.add_worksheet("Two")
            second.write_row(0, 0, ["a", "b"], f["head"])
            sheet2 = sheet_class(second)
            for r in range(1, 12):
                sheet2.begin_row(r)
                sheet2.date(0, dt.datetime(2026, 1, r), f["day"])
                sheet2.number(1, r, f["int"])
                sheet2.end_row()
            sheets.append(sheet2)
            return sheets

        self.same(build)

    def test_the_file_opens_with_the_values_written(self):
        def build(wb, sheet_class):
            f = _formats(wb)
            ws = wb.add_worksheet("Data")
            ws.write_row(0, 0, ["name", "amount", "day"], f["head"])
            sheet = sheet_class(ws)
            sheet.begin_row(1)
            sheet.text(0, " spaced & <tagged> ", f["text"])
            sheet.number(1, 1234.5, f["dec"])
            sheet.date(2, dt.datetime(2026, 11, 3), f["day"])
            sheet.end_row()
            return [sheet]

        path = self.same(build)
        ws = load_workbook(path)["Data"]
        self.assertEqual(ws["A2"].value, " spaced & <tagged> ")
        self.assertEqual((ws["B2"].value, ws["B2"].number_format), (1234.5, "#,##0.00"))
        self.assertEqual(ws["C2"].value, dt.datetime(2026, 11, 3))
        self.assertEqual(ws.dimensions, "A1:C2")


class SwitchTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(fast_xlsx, "_enabled", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ws(self):
        wb = xlsxwriter.Workbook(os.path.join(tempfile.mkdtemp(), "x.xlsx"), {"constant_memory": True})
        return wb, wb.add_worksheet("T")

    def test_it_is_on_by_default_and_checks_itself(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FAST_XLSX", None)
            self.assertTrue(fast_xlsx.enabled())
            _, ws = self.ws()
            self.assertIsInstance(fast_xlsx.new_sheet(ws), fast_xlsx.FastSheet)

    def test_the_switch_turns_it_off(self):
        with patch.dict(os.environ, {"FAST_XLSX": "0"}):
            self.assertFalse(fast_xlsx.enabled())
            _, ws = self.ws()
            self.assertIsInstance(fast_xlsx.new_sheet(ws), fast_xlsx.ClassicSheet)

    def test_a_workbook_that_is_not_constant_memory_gets_the_ordinary_writer(self):
        wb = xlsxwriter.Workbook(os.path.join(tempfile.mkdtemp(), "x.xlsx"))
        self.assertIsInstance(fast_xlsx.new_sheet(wb.add_worksheet("T")), fast_xlsx.ClassicSheet)

    def test_if_it_ever_writes_something_different_it_switches_itself_off(self):
        original = fast_xlsx.FastSheet.number

        def wrong(self, ci, value, fmt=None):
            return original(self, ci, value + 1, fmt)

        with patch.object(fast_xlsx.FastSheet, "number", wrong):
            self.assertFalse(fast_xlsx.enabled())

    def test_if_the_check_cannot_run_it_switches_itself_off(self):
        with patch.object(fast_xlsx, "_sample", side_effect=RuntimeError("boom")):
            self.assertFalse(fast_xlsx.enabled())


if __name__ == "__main__":
    unittest.main()
