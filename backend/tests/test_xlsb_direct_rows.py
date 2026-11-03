"""The .xlsb writer converts plain rows straight from their text; the result must be exactly what the
XML-parser route gives, byte for byte, whatever the workbook holds."""

import datetime as dt
import math
import random
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import openpyxl
import xlsxwriter

from app.services import fast_xlsx, xlsb_writer, xlsx_compact


def _convert(source, destination, direct):
    with patch.object(xlsb_writer, "_FAST_ROWS", direct):
        xlsb_writer.convert(source, destination)
    with zipfile.ZipFile(destination) as z:
        return {name: z.read(name) for name in z.namelist()}


class DirectRowsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def same(self, source, compact=False):
        source = Path(source)
        if compact:
            xlsx_compact.compact_xlsx_in_place(source)
        slow = _convert(source, self.dir / "slow.xlsb", direct=False)
        fast = _convert(source, self.dir / "fast.xlsb", direct=True)
        self.assertEqual(sorted(slow), sorted(fast))
        for name in slow:
            self.assertEqual(slow[name], fast[name], name)

    def book(self, name="t.xlsx", **options):
        path = self.dir / name
        return xlsxwriter.Workbook(str(path), {"constant_memory": True, **options}), path

    def test_plain_text_numbers_dates_and_blanks(self):
        wb, path = self.book()
        ws = wb.add_worksheet("Data")
        text, num, day = (wb.add_format({"font_name": "Calibri", "border": 1}),
                          wb.add_format({"num_format": "#,##0.00", "border": 1}),
                          wb.add_format({"num_format": "DD-MMM-YYYY"}))
        ws.write_row(0, 0, ["a", "b", "c", "d"], wb.add_format({"bold": True}))
        ws.set_column(0, 3, 15)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, 60, 3)
        for r in range(1, 61):
            ws.write_string(r, 0, f"row {r} & <co> ünï {r % 7}", text)
            ws.write_number(r, 1, r * 1.37 if r % 3 else r, num)
            ws.write_datetime(r, 2, dt.datetime(2026, 1, 1) + dt.timedelta(days=r), day)
            ws.write_blank(r, 3, None, text)
        wb.close()
        self.same(path)
        self.same(path, compact=True)

    def test_awkward_text_and_numbers(self):
        wb, path = self.book()
        ws = wb.add_worksheet("Odd")
        for r, value in enumerate(["", " ", "  lead", "trail  ", "a&b", "a&amp;b", "<x>", "q\"u'o", "x\ny", "x\r\ny",
                                   "tab\tx", "_x0041_", "\x00\x0b", "é₹漢字😀", "x" * 5000, "a\rb", "&#13;"], start=1):
            ws.write_string(r, 0, value)
        for r, value in enumerate([0, -0.0, 1, -1, 536870911, 536870912, 0.5, 1e-9, 1e300, 12345.678,
                                   2 ** 40, 1 / 3, 100, 100.0], start=1):
            ws.write_number(r, 1, value)
        wb.close()
        self.same(path)

    def test_rows_with_gaps_and_big_jumps_and_empty_rows(self):
        wb, path = self.book()
        ws = wb.add_worksheet("Gaps")
        ws.write_row(0, 0, ["a", "b", "c", "d", "e"])
        ws.write_string(1, 2, "third only")
        ws.write_number(3, 4, 5)
        ws.write_string(3, 0, "x")
        ws.write_string(40, 1, "after a jump")
        wb.close()
        self.same(path)
        self.same(path, compact=True)

    def test_other_kinds_of_cell_still_go_through_the_parser(self):
        wb, path = self.book()
        ws = wb.add_worksheet("Mixed")
        ws.write_row(0, 0, ["a", "b", "c", "d", "e", "f"])
        for r in range(1, 30):
            ws.write_string(r, 0, f"text {r}")
            ws.write_number(r, 1, r)
            ws.write_formula(r, 2, f"=B{r + 1}*2", None, r * 2)
            ws.write_boolean(r, 3, r % 2 == 0)
            ws.write_formula(r, 4, "=1/0", None, "#DIV/0!")
        wb.close()
        self.same(path)

    def test_what_it_cannot_convert_is_refused_the_same_way_by_both_routes(self):
        wb, path = self.book()
        ws = wb.add_worksheet("Rich")
        ws.write_row(0, 0, ["a", "b"])
        for r in range(1, 5):
            ws.write_number(r, 0, r)
            ws.write_rich_string(r, 1, "plain ", wb.add_format({"bold": True}), "bold")
        wb.close()
        for direct in (False, True):
            with patch.object(xlsb_writer, "_FAST_ROWS", direct), self.assertRaises(xlsb_writer.Unsupported) as caught:
                xlsb_writer.convert(path, self.dir / "x.xlsb")
            self.assertIn("worksheet element r", str(caught.exception))
        self.assertFalse((self.dir / "x.xlsb").exists())              # no half-written file left behind

    def test_several_sheets_share_one_string_table(self):
        wb, path = self.book()
        for name in ("One", "Two", "Three"):
            ws = wb.add_worksheet(name)
            ws.write_row(0, 0, ["h1", "h2"])
            for r in range(1, 40):
                ws.write_string(r, 0, f"shared {r % 5}")
                ws.write_string(r, 1, f"{name} {r}")
        wb.close()
        self.same(path)

    def test_a_workbook_that_is_not_constant_memory_and_one_from_openpyxl(self):
        wb = xlsxwriter.Workbook(str(self.dir / "normal.xlsx"))
        ws = wb.add_worksheet("N")
        for r in range(50):
            ws.write_string(r, 0, f"s{r % 9}")
            ws.write_number(r, 1, r / 7)
        wb.close()
        self.same(self.dir / "normal.xlsx")

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["name", "amount", "note"])
        for r in range(40):
            sheet.append([f"cust {r % 6}", r * 2.5, None if r % 4 else "x & y"])
        book.save(self.dir / "openpyxl.xlsx")
        self.same(self.dir / "openpyxl.xlsx")

    def test_the_fast_row_writer_and_the_direct_converter_together(self):
        wb, path = self.book()
        ws = wb.add_worksheet("Fast")
        text = wb.add_format({"border": 1})
        num = wb.add_format({"num_format": "0.00", "border": 1})
        ws.write_row(0, 0, ["a", "b", "c"])
        sheet = fast_xlsx.FastSheet(ws)
        rng = random.Random(3)
        for r in range(1, 300):
            sheet.begin_row(r)
            sheet.text(0, rng.choice(["alpha", "be&ta", "  pad ", "x\r\ny", "é"]), text)
            sheet.number(1, rng.choice([0, 1.5, -2, 1e10, math.pi]), num)
            if r % 5:
                sheet.text(2, "tail", text)
            sheet.end_row()
        fast_xlsx.close_workbook(wb, [sheet])
        self.same(path)
        self.same(path, compact=True)


if __name__ == "__main__":
    unittest.main()
