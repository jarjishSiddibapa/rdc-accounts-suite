"""The pure-Python .xlsx -> .xlsb writer (app.services.xlsb_writer).

Each test builds a small workbook with openpyxl, converts it, and reads the
result back with pyxlsb, an independent reader, to check that nothing changed.
"""

import datetime as dt
import tempfile
import unittest
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from pyxlsb import open_workbook

from app.services import xlsb_convert, xlsb_writer, xlsx_compact


def _read(path):
    """{sheet name: {(row, col): value}} for every cell that has a value."""
    result = {}
    with open_workbook(str(path)) as book:
        for name in book.sheets:
            cells = {}
            with book.get_sheet(name) as sheet:
                for row in sheet.rows(sparse=True):
                    for cell in row:
                        if cell.v is not None:
                            cells[(cell.r, cell.c)] = cell.v
            result[name] = cells
    return result


def _numeric_serial(value):
    return (value - dt.datetime(1899, 12, 30)).total_seconds() / 86400


class XlsbWriterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _convert(self, workbook, compact=False):
        source = self.dir / "in.xlsx"
        workbook.save(source)
        if compact:
            xlsx_compact.compact_xlsx_in_place(source)
        target = self.dir / "out.xlsb"
        xlsb_writer.convert(source, target)
        return target

    def test_every_value_comes_through_unchanged(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        values = [
            "text", "ünïcödé ₹ 日本 😀", "  padded  ", "line1\nline2", "5", "-", 0, 1, -42, 536870911,
            -536870912, 536870912, 123456789012345, 8.0, 2.5, 0.1, -0.001, 1e300, -1.5e-300, True, False,
        ]
        for row, value in enumerate(values, 1):
            sheet.cell(row, 1, value)
        sheet["B1"] = dt.datetime(2026, 10, 19, 13, 45, 30)
        sheet["B2"] = dt.date(1999, 12, 31)
        sheet["C5"] = "gap before this cell"
        got = _read(self._convert(workbook))["Data"]
        for row, value in enumerate(values, 1):
            expected = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
            if isinstance(value, bool):
                expected = value
            self.assertEqual(got[(row - 1, 0)], expected, f"row {row}")
        self.assertAlmostEqual(got[(0, 1)], _numeric_serial(dt.datetime(2026, 10, 19, 13, 45, 30)), places=6)
        self.assertEqual(got[(4, 2)], "gap before this cell")
        self.assertNotIn((3, 2), got)

    def test_a_compacted_workbook_without_cell_addresses_converts_too(self):
        workbook = openpyxl.Workbook(write_only=True)
        sheet = workbook.create_sheet("Rows")
        for row in range(200):
            sheet.append([f"name {row % 7}", row, row * 1.5, None, "-"])
        got = _read(self._convert(workbook, compact=True))["Rows"]
        self.assertEqual(got[(199, 0)], "name 3")
        self.assertEqual(got[(199, 1)], 199.0)
        self.assertEqual(got[(199, 2)], 298.5)
        self.assertEqual(got[(199, 4)], "-")
        self.assertNotIn((199, 3), got)

    def test_formulas_keep_their_text_and_get_their_value(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet["A1"], sheet["B1"], sheet["C1"] = 10, 4, 1.005
        sheet["D1"] = "=ROUND(A1-B1-1,2)"
        sheet["D2"] = "=SUBTOTAL(9,A1:C1)"
        sheet["D3"] = "=ABS(-A1)+(2*3)/4"
        sheet["D4"] = "=1/0"
        sheet["D5"] = "=ROUND(C1,2)"
        got = _read(self._convert(workbook))["Sheet"]
        self.assertEqual(got[(0, 3)], 5.0)
        self.assertAlmostEqual(got[(1, 3)], 15.005)
        self.assertEqual(got[(2, 3)], 11.5)
        self.assertEqual(got[(4, 3)], 1.01)           # Excel rounds half away from zero
        self.assertEqual(got[(3, 3)], "0x7")             # pyxlsb shows an error code as hex: #DIV/0!

    def test_text_in_a_calculation_gives_the_error_excel_would(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet["A1"], sheet["B1"] = "-", 5
        sheet["C1"] = "=ROUND(B1-A1,2)"
        self.assertEqual(_read(self._convert(workbook))["Sheet"][(0, 2)], "0xf")   # #VALUE!

    def test_sheets_keep_names_order_and_visibility(self):
        workbook = openpyxl.Workbook()
        workbook.active.title = "First"
        workbook.create_sheet("Ünï'code (name)")["A1"] = "x"
        workbook.create_sheet("Hidden").sheet_state = "hidden"
        target = self._convert(workbook)
        with open_workbook(str(target)) as book:
            self.assertEqual(book.sheets, ["First", "Ünï'code (name)", "Hidden"])

    def test_a_workbook_with_shared_strings_converts(self):
        import xlsxwriter                      # writes a shared string table, unlike openpyxl

        source = self.dir / "shared.xlsx"
        book = xlsxwriter.Workbook(str(source))
        sheet = book.add_worksheet("Shared")
        bold = book.add_format({"bold": True, "num_format": "0.00"})
        for row in range(29):
            sheet.write(row, 0, f"repeat {row % 3}")
        sheet.write(29, 0, 3.14159, bold)
        sheet.write(30, 0, "")
        book.close()
        with zipfile.ZipFile(source) as archive:
            self.assertIn("xl/sharedStrings.xml", archive.namelist())
        target = self.dir / "shared.xlsb"
        xlsb_writer.convert(source, target)
        got = _read(target)["Shared"]
        self.assertEqual({got[(row, 0)] for row in range(29)}, {"repeat 0", "repeat 1", "repeat 2"})
        self.assertEqual(got[(29, 0)], 3.14159)

    def test_formatting_is_written_to_the_binary_styles(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        side = Side("thin", color="FFD0D8E4")
        sheet["A1"] = "styled"
        sheet["A1"].font = Font(name="Segoe UI", bold=True, size=10, color="FFFFFFFF")
        sheet["A1"].fill = PatternFill("solid", fgColor="FF1F3864")
        sheet["A1"].border = Border(left=side, right=side, top=side, bottom=side)
        sheet["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet["A2"] = 5
        sheet["A2"].number_format = '#,##0;-#,##0;"-"'
        sheet.column_dimensions["A"].width = 30
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = "A1:A2"
        target = self._convert(workbook)
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            self.assertTrue({"xl/workbook.bin", "xl/styles.bin", "xl/sharedStrings.bin", "xl/worksheets/sheet1.bin"} <= names)
            styles = archive.read("xl/styles.bin")
        self.assertIn("Segoe UI".encode("utf-16-le"), styles)
        self.assertIn('#,##0;-#,##0;"-"'.encode("utf-16-le"), styles)

    def test_features_it_cannot_convert_are_refused_not_dropped(self):
        merged = openpyxl.Workbook()
        merged.active["A1"] = "x"
        merged.active.merge_cells("A1:B1")
        conditional = openpyxl.Workbook()
        conditional.active["A1"] = 1
        conditional.active.conditional_formatting.add("A1", CellIsRule(operator="greaterThan", formula=["0"], font=Font(bold=True)))
        text_formula = openpyxl.Workbook()
        text_formula.active["A1"] = '="text"'
        unknown_function = openpyxl.Workbook()
        unknown_function.active["A1"] = "=VLOOKUP(1,A1:B2,2,0)"
        for workbook in (merged, conditional, text_formula, unknown_function):
            with self.assertRaises(xlsb_writer.Unsupported):
                self._convert(workbook)
            self.assertFalse((self.dir / "out.xlsb").exists())
            self.assertEqual([p.name for p in self.dir.glob("*.partial")], [])

    def test_convert_to_xlsb_reports_success_and_failure_without_raising(self):
        workbook = openpyxl.Workbook()
        workbook.active["A1"] = "ok"
        source = self.dir / "ok.xlsx"
        workbook.save(source)
        target = self.dir / "ok.xlsb"
        self.assertTrue(xlsb_convert.convert_to_xlsb(source, target))
        self.assertEqual(_read(target)["Sheet"][(0, 0)], "ok")

        bad = openpyxl.Workbook()
        bad.active.merge_cells("A1:B1")
        bad.save(self.dir / "bad.xlsx")
        self.assertFalse(xlsb_convert.convert_to_xlsb(self.dir / "bad.xlsx", self.dir / "bad.xlsb"))
        self.assertFalse((self.dir / "bad.xlsb").exists())
        self.assertFalse(xlsb_convert.convert_to_xlsb(self.dir / "missing.xlsx", self.dir / "missing.xlsb"))

    def test_a_large_sheet_is_much_smaller_as_xlsb(self):
        workbook = openpyxl.Workbook(write_only=True)
        sheet = workbook.create_sheet("Big")
        for row in range(20000):
            sheet.append([f"customer {row % 500}", row, row * 0.25, dt.datetime(2026, 1, 1) + dt.timedelta(days=row % 300)])
        source = self.dir / "big.xlsx"
        workbook.save(source)
        xlsx_compact.compact_xlsx_in_place(source)
        target = self.dir / "big.xlsb"
        xlsb_writer.convert(source, target)
        self.assertLess(target.stat().st_size, source.stat().st_size)
        got = _read(target)["Big"]
        self.assertEqual(len(got), 20000 * 4)
        self.assertEqual(got[(19999, 1)], 19999.0)


if __name__ == "__main__":
    unittest.main()
