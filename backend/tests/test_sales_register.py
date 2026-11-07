"""RDC Sales Register Report: the reporting period, the workbook written exactly
as the ERP returned the rows, one file per month, access by permission, and the
richer schedule and fixed sender it comes with."""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openpyxl
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import permissions, security
from app.database import Base
from app.jobs import JobUserError
from app.models import EmailSettings, ScheduledReportMail, SystemEmailSettings, User
from app.regional import IST
from app.services import scheduled_report_mail as mail
from app.services.sales_register import auto_mail, report
from app.services.sales_register.sql import SALES_REGISTER_SQL


class PeriodTests(unittest.TestCase):
    def test_a_normal_day_reports_the_month_so_far_through_yesterday(self):
        self.assertEqual(report.get_report_periods(date(2026, 10, 30)),
                         [("Current Month MTD", date(2026, 10, 1), date(2026, 10, 29))])
        self.assertEqual(report.get_report_periods(date(2026, 10, 9)),
                         [("Current Month MTD", date(2026, 10, 1), date(2026, 10, 8))])

    def test_the_first_of_a_month_reports_the_whole_previous_month_and_nothing_else(self):
        self.assertEqual(report.get_report_periods(date(2026, 10, 1)),
                         [("Previous Month", date(2026, 9, 1), date(2026, 9, 30))])
        self.assertEqual(report.get_report_periods(date(2027, 1, 1)),
                         [("Previous Month", date(2026, 12, 1), date(2026, 12, 31))])
        self.assertEqual(report.get_report_periods(date(2028, 3, 1)),
                         [("Previous Month", date(2028, 2, 1), date(2028, 2, 29))])

    def test_through_the_eighth_the_previous_month_and_the_month_so_far_are_separate_periods(self):
        self.assertEqual(report.get_report_periods(date(2026, 10, 8)),
                         [("Previous Month", date(2026, 9, 1), date(2026, 9, 30)),
                          ("Current Month MTD", date(2026, 10, 1), date(2026, 10, 7))])
        self.assertEqual(len(report.get_report_periods(date(2026, 10, 9))), 1)
        self.assertEqual(report.get_report_periods(date(2026, 11, 2)),
                         [("Previous Month", date(2026, 10, 1), date(2026, 10, 31)),
                          ("Current Month MTD", date(2026, 11, 1), date(2026, 11, 1))])
        self.assertEqual(report.get_report_periods(date(2026, 10, 5)),
                         [("Previous Month", date(2026, 9, 1), date(2026, 9, 30)),
                          ("Current Month MTD", date(2026, 10, 1), date(2026, 10, 4))])
        self.assertEqual(report.get_report_periods(date(2027, 1, 3)),
                         [("Previous Month", date(2026, 12, 1), date(2026, 12, 31)),
                          ("Current Month MTD", date(2027, 1, 1), date(2027, 1, 2))])

    def test_each_period_is_within_one_month_so_no_file_mixes_months(self):
        day = date(2026, 1, 1)
        for _ in range(800):
            periods = report.get_report_periods(day)
            self.assertTrue(1 <= len(periods) <= 2)
            for _, start, end in periods:
                self.assertLessEqual(start, end)
                self.assertEqual((start.year, start.month), (end.year, end.month))
            self.assertEqual(len(periods), 2 if 2 <= day.day <= 8 else 1)
            day += timedelta(days=1)

    def test_the_query_is_the_supplied_one_with_its_binds(self):
        for bind in (":p_from_date", ":p_to_date + 1", ":p_org_id", ":p_trkinv", ":p_location",
                     ":p_cust_name", ":p_cust_number"):
            self.assertIn(bind, SALES_REGISTER_SQL)
        self.assertIn("rct.complete_flag = 'Y'", SALES_REGISTER_SQL)
        self.assertEqual(report.P_ORG_ID, 102)


class _FakeCursor:
    def __init__(self, columns, rows):
        self.description = [(c,) for c in columns]
        self.rows = list(rows)
        self.executed = None
        self.arraysize = 0
        self.prefetchrows = 0

    def execute(self, sql, **binds):
        self.executed = (sql, binds)

    def fetchmany(self, size):
        batch, self.rows = self.rows[:size], self.rows[size:]
        return batch

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.outputtypehandler = None

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def _run_build(columns, rows, path, layout=None):
    """Runs build_workbook on a fake cursor. Unless a layout is given the workbook
    simply mirrors the cursor's columns, so the writing can be tested on its own."""
    cursor = _FakeCursor(columns, rows)
    layout = [(c, c) for c in columns] if layout is None else layout
    with patch.object(report, "_init_oracle_client"), \
            patch.object(report, "_connect_oracle", lambda cfg: _FakeConnection(cursor)), \
            patch.object(report, "TEMPLATE_COLUMNS", layout), \
            patch.object(report, "_FETCH_ROWS", 2):
        summary = report.build_workbook(SimpleNamespace(instant_client_dir=""), date(2026, 10, 1),
                                        date(2026, 10, 29), str(path))
    return summary, cursor


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_every_value_is_written_exactly_as_the_erp_returned_it(self):
        columns = ["INVOICE_NUMBER", "INVOICE_DATE", "LINE_AMOUNT", "QTY", "SALES_ORDER_DATE", "NOTE", "GSTIN"]
        stamp = datetime(2026, 10, 5, 13, 45, 7)
        rows = [
            ("0012345", "05-OCT-2026", 1234.5, 3, stamp, "=SUM(A1:A2)", None),       # text that looks like a number / formula
            ("INV/9", "06-OCT-2026", -0.1, 0, None, "http://example.com", "27AAAAA0000A1Z5"),
            ("X" * 300, "07-OCT-2026", 10, 1, stamp, "multi\nline", ""),
        ]
        path = self.dir / "r.xlsx"
        summary, cursor = _run_build(columns, rows, path)
        self.assertEqual(summary, {"rows": 3, "columns": 7, "sheets": 1})

        ws = openpyxl.load_workbook(path)[report.SHEET_NAME]
        self.assertEqual([c.value for c in ws[1]], columns)
        first, second, third = ([c.value for c in ws[i]] for i in (2, 3, 4))
        self.assertEqual(first, ["0012345", "05-OCT-2026", 1234.5, 3, stamp, "=SUM(A1:A2)", None])
        self.assertIsInstance(first[0], str)               # leading zeros kept: text stays text
        self.assertEqual(first[5], "=SUM(A1:A2)")           # never turned into a formula
        self.assertEqual(ws["F2"].data_type, "s")
        self.assertEqual(second, ["INV/9", "06-OCT-2026", -0.1, 0, None, "http://example.com", "27AAAAA0000A1Z5"])
        self.assertIsNone(ws["F3"].hyperlink)               # nor into a link
        self.assertEqual(third[0], "X" * 300)
        self.assertEqual(third[5], "multi\nline")
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(ws.auto_filter.ref, "A1:G4")

    def test_the_workbook_has_the_templates_columns_in_its_order_and_nothing_else(self):
        # the cursor returns the query's order, with columns the template does not use
        returned = ["SALES_ORDER_NUM", "GSTIN", "LINE_AMOUNT", "SOURCE", "INVOICE_NUMBER", "GL_DATE"]
        rows = [("SO-1", "27AAAAA0000A1Z5", 99, "Manual", "0012345", "05-OCT-2026")]
        layout = [("INVOICE_NUMBER", "Transaction Number"), ("GSTIN", "RDC GSTIN"), ("CUST_PAN_NUM", "Cust PAN Number"),
                  ("GL_DATE", "GL Date"), ("SOURCE", "Source")]
        path = self.dir / "t.xlsx"
        summary, _ = _run_build(returned, rows, path, layout=layout)
        self.assertEqual(summary["columns"], 5)
        ws = openpyxl.load_workbook(path)[report.SHEET_NAME]
        self.assertEqual([c.value for c in ws[1]], ["Transaction Number", "RDC GSTIN", "Cust PAN Number", "GL Date", "Source"])
        # the PAN is not in the query, so its column is empty; every other value is as returned
        self.assertEqual([c.value for c in ws[2]], ["0012345", "27AAAAA0000A1Z5", None, "05-OCT-2026", "Manual"])
        self.assertEqual(ws.auto_filter.ref, "A1:E2")

    def test_the_real_template_matches_the_erp_report(self):
        import re
        from app.services.sales_register.sql import SALES_REGISTER_SQL
        headings = [heading for _, heading in report.TEMPLATE_COLUMNS]
        self.assertEqual(len(headings), 50)
        self.assertEqual(len(set(headings)), 50)
        self.assertEqual(headings[:6], ["RDC GSTIN", "Source", "Transaction Number", "Comments", "Invoice Date", "GL Date"])
        self.assertEqual(headings[-2:], ["Location", "Sales Person"])
        # every column but the PAN is one the query really returns (its output name ends a select item)
        sql = re.sub(r"--[^\n]*", "", SALES_REGISTER_SQL).upper()
        for column, heading in report.TEMPLATE_COLUMNS:
            if column == "CUST_PAN_NUM":
                self.assertNotIn(column, sql)
                continue
            self.assertRegex(sql, rf"[\s\)\.,']{column}\s*(,|/\*|\bFROM\b)", f"{heading} ({column})")

    def test_the_query_runs_with_the_fixed_org_and_the_dates_and_no_other_filter(self):
        path = self.dir / "r.xlsx"
        _, cursor = _run_build(["A"], [("x",)], path)
        sql, binds = cursor.executed
        self.assertIs(sql, SALES_REGISTER_SQL)
        self.assertEqual(binds, {
            "p_from_date": datetime(2026, 10, 1), "p_to_date": datetime(2026, 10, 29),
            "p_trkinv": None, "p_location": None, "p_cust_name": None, "p_cust_number": None, "p_org_id": 102,
        })

    def test_an_empty_period_still_gives_a_workbook_with_the_headings(self):
        path = self.dir / "r.xlsx"
        summary, _ = _run_build(["A", "B"], [], path)
        self.assertEqual(summary["rows"], 0)
        ws = openpyxl.load_workbook(path)[report.SHEET_NAME]
        self.assertEqual([c.value for c in ws[1]], ["A", "B"])
        self.assertEqual(ws.max_row, 1)

    def test_more_rows_than_a_sheet_hold_continue_on_another_sheet_of_the_same_file(self):
        rows = [(i,) for i in range(1, 8)]
        path = self.dir / "r.xlsx"
        with patch.object(report, "_MAX_SHEET_ROWS", 4):         # header + 3 rows a sheet
            summary, _ = _run_build(["N"], rows, path)
        self.assertEqual((summary["rows"], summary["sheets"]), (7, 3))
        wb = openpyxl.load_workbook(path)
        self.assertEqual(wb.sheetnames, [report.SHEET_NAME, f"{report.SHEET_NAME} (2)", f"{report.SHEET_NAME} (3)"])
        values = [c.value for name in wb.sheetnames for (c,) in wb[name].iter_rows(min_row=2)]
        self.assertEqual(values, list(range(1, 8)))
        self.assertEqual([wb[n].cell(1, 1).value for n in wb.sheetnames], ["N", "N", "N"])

    def test_lobs_are_read_as_text_and_unwritable_characters_dropped(self):
        self.assertEqual(report._cell(b"caf\xc3\xa9"), "café")
        self.assertEqual(report._cell("a\ud800b￾"), "ab")
        self.assertEqual(report._cell("plain"), "plain")
        self.assertEqual(report._cell(12.5), 12.5)
        self.assertIsNone(report._cell(None))


class OneFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _file(self, size):
        path = self.dir / "r.xlsx"
        path.write_bytes(b"x" * size)
        return path

    def test_a_report_within_the_limit_goes_as_it_is(self):
        path = self._file(100)
        groups, how, finals = auto_mail.attachment_fit.fit_together([path])
        self.assertEqual((groups, how, finals), ([[str(path)]], "complete workbook", [path]))

    def test_too_big_but_fine_once_compacted(self):
        path = self._file(2_000_000)
        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1_000_000), \
                patch.object(auto_mail.attachment_fit.xlsx_compact, "compact_xlsx_in_place",
                             lambda p: Path(p).write_bytes(b"y" * 500)):
            groups, how, finals = auto_mail.attachment_fit.fit_together([path])
        self.assertEqual(finals, [path])
        self.assertIn("compacted", how)

    def test_still_too_big_becomes_xlsb(self):
        path = self._file(2_000_000)

        def fake_xlsb(source, destination):
            Path(destination).write_bytes(b"z" * 300)
            return True

        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1_000_000), \
                patch.object(auto_mail.attachment_fit.xlsx_compact, "compact_xlsx_in_place"), \
                patch.object(auto_mail.attachment_fit.xlsb_convert, "convert_to_xlsb", fake_xlsb):
            groups, how, finals = auto_mail.attachment_fit.fit_together([path])
        self.assertEqual(finals[0].suffix, ".xlsb")
        self.assertIn(".xlsb", how)

    def test_the_day_ranges_cover_the_month_exactly_with_no_gap_and_no_overlap(self):
        for count in (2, 3, 7, 31):
            ranges = auto_mail._day_ranges(date(2026, 10, 1), date(2026, 10, 31), count)
            self.assertEqual(len(ranges), count)
            self.assertEqual(ranges[0][0], date(2026, 10, 1))
            self.assertEqual(ranges[-1][1], date(2026, 10, 31))
            for (_, last), (first, _) in zip(ranges, ranges[1:]):
                self.assertEqual(first, last + timedelta(days=1))

    def test_the_real_converter_keeps_values_types_and_dates(self):
        """A real workbook through the real compaction and .xlsb conversion."""
        import pyxlsb
        columns = ["INV", "AMT", "WHEN", "NOTE"]
        stamp = datetime(2026, 10, 5, 13, 45)
        rows = [("0012", 1234.5, stamp, "a & b <c>"), ("7", 10, None, "=1+1"), ("", 0, stamp, "multi\nline")]
        path = self.dir / "real.xlsx"
        _run_build(columns, rows, path)
        xlsx_values = [[c.value for c in row] for row in openpyxl.load_workbook(path).active.iter_rows()]

        from app.services import xlsb_writer
        binary = self.dir / "real.xlsb"
        xlsb_writer.convert(str(path), str(binary))
        with pyxlsb.open_workbook(str(binary)) as wb, wb.get_sheet(1) as sheet:
            got = [[c.v for c in row] for row in sheet.rows()]
        self.assertEqual([r[0] for r in got], [r[0] for r in xlsx_values])
        self.assertEqual([r[1] for r in got][1:], [1234.5, 10.0, 0.0])
        self.assertEqual([r[3] for r in got][1:], ["a & b <c>", "=1+1", "multi\nline"])


class MonthFilesTests(unittest.TestCase):
    """auto_mail.build: one file per month, the files in one mail while they fit."""

    def _build(self, today, sizes=None, size_of=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        built = []

        def fake_cpu_phase(fn, cfg, start, end, path, progress_cb=None):
            Path(path).write_bytes(b"x" * (size_of(start, end) if size_of else (sizes or {}).get(start, 100)))
            built.append((start, end))
            return {"rows": 10 * len(built), "columns": 50, "sheets": 1}

        with patch.object(auto_mail, "SCRATCH_DIR", Path(tmp.name)), \
                patch.object(auto_mail, "now_ist", lambda: datetime(today.year, today.month, today.day, 9, 0)), \
                patch.object(auto_mail, "run_cpu_phase", fake_cpu_phase), \
                patch.object(auto_mail, "oracle_config", lambda: None):
            payload = auto_mail.build({})
        return payload, built

    def test_early_in_a_month_each_month_is_its_own_file_in_one_mail(self):
        payload, built = self._build(date(2026, 11, 2))
        self.assertEqual(built, [(date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 11, 1), date(2026, 11, 1))])
        self.assertEqual(len(payload.attachment_groups), 1)
        names = [Path(p).name for p in payload.attachment_groups[0]]
        self.assertEqual(names, ["RDC_Sales_Register_Report_2026-10-01_to_2026-10-31.xlsx",
                                 "RDC_Sales_Register_Report_2026-11-01_to_2026-11-01.xlsx"])
        self.assertEqual(payload.values["from_date"], "01-Oct-2026")
        self.assertEqual(payload.values["to_date"], "01-Nov-2026")
        self.assertEqual(payload.values["rows"], "30")
        self.assertEqual(payload.values["period"], "Previous Month + Current Month MTD")
        self.assertEqual(len(payload.downloads), 2)
        self.assertEqual(payload.detail["attachment"], "2 files, one per month")
        self.assertEqual(payload.notes, [])              # no extra explanation in the mail
        text = mail.render_text(mail.SPECS[auto_mail.APP_KEY].default_intro, payload.values)
        self.assertIn("attached the RDC Sales Register Report files for 01-Oct-2026 to 01-Nov-2026", text)

    def test_later_in_the_month_it_is_one_file(self):
        payload, built = self._build(date(2026, 11, 9))
        self.assertEqual(built, [(date(2026, 11, 1), date(2026, 11, 8))])
        self.assertEqual([len(g) for g in payload.attachment_groups], [1])
        self.assertEqual(payload.detail["attachment"], "complete workbook")
        text = mail.render_text(mail.SPECS[auto_mail.APP_KEY].default_intro, payload.values)
        self.assertIn("attached the RDC Sales Register Report for 01-Nov-2026", text)

    def test_the_first_of_a_month_is_the_previous_month_only(self):
        payload, built = self._build(date(2026, 11, 1))
        self.assertEqual(built, [(date(2026, 10, 1), date(2026, 10, 31))])
        self.assertEqual(payload.values["period"], "Previous Month")

    def test_files_too_big_together_are_made_smaller_and_still_go_in_one_mail(self):
        # one email carries a limited TOTAL, so the files are shrunk together (.xlsb) rather than split
        def fake_xlsb(source, destination):
            Path(destination).write_bytes(b"y" * 100)
            return True

        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1000), \
                patch.object(auto_mail.attachment_fit.xlsx_compact, "compact_xlsx_in_place"), \
                patch.object(auto_mail.attachment_fit.xlsb_convert, "convert_to_xlsb", fake_xlsb):
            payload, _ = self._build(date(2026, 11, 2), sizes={date(2026, 10, 1): 700, date(2026, 11, 1): 700})
        self.assertEqual(len(payload.attachment_groups), 1)
        names = [Path(p).name for p in payload.attachment_groups[0]]
        self.assertEqual(len(names), 2)
        self.assertTrue(any(name.endswith(".xlsb") for name in names))
        self.assertEqual(sorted(Path(p).name for _, p in payload.downloads), sorted(names))   # the preview offers what is sent
        self.assertIn(".xlsb", payload.detail["attachment"])

    def test_a_file_already_converted_to_xlsb_is_left_alone_not_converted_again_or_deleted(self):
        from app.services import attachment_fit
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        binary = Path(tmp.name) / "month.xlsb"
        binary.write_bytes(b"b" * 700)
        other = Path(tmp.name) / "other.xlsx"
        other.write_bytes(b"x" * 700)
        converted = []

        def fake_xlsb(source, destination):
            converted.append(Path(source).name)
            Path(destination).write_bytes(b"y" * 100)
            return True

        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1000), \
                patch.object(attachment_fit.xlsx_compact, "compact_xlsx_in_place"), \
                patch.object(attachment_fit.xlsb_convert, "convert_to_xlsb", fake_xlsb):
            groups, how, finals = attachment_fit.fit_together([binary, other])
        self.assertEqual(converted, ["other.xlsx"])               # only the .xlsx was converted
        self.assertTrue(binary.is_file())                          # and the .xlsb is still there, untouched
        self.assertEqual(binary.read_bytes(), b"b" * 700)
        self.assertEqual(len(groups), 1)

    def test_a_failed_conversion_never_deletes_a_file_that_is_already_xlsb(self):
        from app.services import attachment_fit
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        binary = Path(tmp.name) / "month.xlsb"
        binary.write_bytes(b"b" * 700)
        other = Path(tmp.name) / "other.xlsb"
        other.write_bytes(b"x" * 700)
        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1000), \
                patch.object(attachment_fit.xlsb_convert, "convert_to_xlsb", lambda s, d: False):
            groups, _, _ = attachment_fit.fit_together([binary, other])
        self.assertTrue(binary.is_file() and other.is_file())
        self.assertEqual(len(groups), 2)                           # cannot share one mail: two, nothing dropped

    def test_only_when_they_still_cannot_share_a_mail_are_they_spread_over_several(self):
        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1000), \
                patch.object(auto_mail.attachment_fit.xlsx_compact, "compact_xlsx_in_place"), \
                patch.object(auto_mail.attachment_fit.xlsb_convert, "convert_to_xlsb", lambda s, d: False):
            payload, _ = self._build(date(2026, 11, 2), sizes={date(2026, 10, 1): 700, date(2026, 11, 1): 700})
        self.assertEqual([len(g) for g in payload.attachment_groups], [1, 1])


    def test_a_month_too_big_on_its_own_even_as_xlsb_is_split_into_date_ranges_not_dropped(self):
        # 100 bytes a day: October (3,100) is over the 1,000 limit, a quarter of it (~800) fits
        no_shrink = (patch.object(auto_mail.attachment_fit.xlsx_compact, "compact_xlsx_in_place"),
                     patch.object(auto_mail.attachment_fit.xlsb_convert, "convert_to_xlsb", lambda s, d: False))
        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1000), no_shrink[0], no_shrink[1]:
            payload, built = self._build(date(2026, 11, 2), size_of=lambda s, e: ((e - s).days + 1) * 100)
        sent_files = [Path(p) for g in payload.attachment_groups for p in g]
        # every file within the limit, every day of October in exactly one part, November untouched
        self.assertTrue(all(f.stat().st_size <= 1000 for f in sent_files))
        october = sorted(f.name for f in sent_files if "2026-10-" in f.name)
        self.assertGreaterEqual(len(october), 4)
        covered = []
        for name in october:
            first, last = name.removeprefix("RDC_Sales_Register_Report_").removesuffix(".xlsx").split("_to_")
            covered.append((date.fromisoformat(first), date.fromisoformat(last)))
        covered.sort()
        self.assertEqual(covered[0][0], date(2026, 10, 1))
        self.assertEqual(covered[-1][1], date(2026, 10, 31))
        for (_, last), (first, _) in zip(covered, covered[1:]):
            self.assertEqual(first, last + timedelta(days=1))
        self.assertEqual(sum(1 for f in sent_files if "2026-11-01" in f.name), 1)
        self.assertEqual(len(payload.downloads), len(sent_files))
        self.assertIn("split", payload.detail["attachment"])
        self.assertEqual(payload.notes, [])

    def test_a_single_day_that_is_still_too_big_stops_with_a_clear_message(self):
        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 1000), \
                patch.object(auto_mail.attachment_fit.xlsx_compact, "compact_xlsx_in_place"), \
                patch.object(auto_mail.attachment_fit.xlsb_convert, "convert_to_xlsb", lambda s, d: False):
            with self.assertRaises(JobUserError) as caught:
                self._build(date(2026, 11, 2), size_of=lambda s, e: 5000)       # even one day is over the limit
        self.assertIn("Nothing was sent", str(caught.exception))


class AccessTests(unittest.TestCase):
    KEY = "rdc-sales-register-report"

    def test_it_is_a_registered_application_for_the_report_mail(self):
        self.assertIn(self.KEY, permissions.APP_KEYS)
        self.assertEqual(permissions.APP_LABELS[self.KEY], "RDC Sales Register Report")
        self.assertIn(self.KEY, permissions.REPORT_RECIPIENT_APP_KEYS)
        spec = mail.SPECS[self.KEY]
        self.assertTrue(spec.scheduled)
        mail.validate_template(spec.default_subject, spec.placeholders)
        mail.validate_template(spec.default_intro, spec.placeholders)
        self.assertIs(mail._job_for(self.KEY), auto_mail.run_auto_mail_job)

    def test_a_regular_user_can_use_it_when_granted_and_only_then(self):
        granted = SimpleNamespace(role="user", allowed_apps='["%s", "gst-invoice-adder"]' % self.KEY)
        self.assertEqual(permissions.parse_allowed_apps(granted), [self.KEY, "gst-invoice-adder"])
        self.assertTrue(permissions.user_has_app_access(granted, self.KEY))
        other = SimpleNamespace(role="user", allowed_apps='["gst-invoice-adder"]')
        self.assertFalse(permissions.user_has_app_access(other, self.KEY))
        self.assertTrue(permissions.user_has_app_access(SimpleNamespace(role="admin", allowed_apps=None), self.KEY))

    def test_everything_under_it_follows_its_permission_not_admin_only(self):
        from app.routers import rdc_sales_register as routes
        self.assert_follows_permission(self.KEY, routes)

    def test_the_other_fixed_sender_mailers_follow_their_permission_too(self):
        from app.routers import debtors_mailer, unidentified_entries
        self.assert_follows_permission("debtors-reports-auto-mailer", debtors_mailer)
        self.assert_follows_permission("unidentified-entries", unidentified_entries)

    def assert_follows_permission(self, key, routes):
        from fastapi import FastAPI
        import asyncio
        from app import auth
        app = FastAPI()
        app.include_router(routes.router)
        base = f"/api/tools/{key}"
        user = SimpleNamespace(role="user", allowed_apps='["%s"]' % key)
        app.dependency_overrides[auth.get_current_user] = lambda: user

        def status(method, path):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"{}", "more_body": False}

            async def send(message):
                sent.append(message)

            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                     "path": base + path, "raw_path": (base + path).encode(), "query_string": b"",
                     "headers": [(b"content-type", b"application/json")], "server": ("test", 80),
                     "client": ("test", 1), "scheme": "http", "root_path": ""}
            try:
                asyncio.run(app(scope, receive, send))
            except Exception:                    # past the permission check, the database is not there
                return None
            return next(m["status"] for m in sent if m["type"] == "http.response.start")

        calls = (("GET", "/auto-mail"), ("GET", "/auto-mail/log"), ("PUT", "/auto-mail"),
                 ("POST", "/auto-mail/send-now"), ("GET", "/mail/defaults"))
        for method, path in calls:               # granted: not refused
            self.assertNotEqual(status(method, path), 403, (method, path))
        user.allowed_apps = '["gst-invoice-adder"]'                      # not granted: refused everywhere
        for method, path in calls:
            self.assertEqual(status(method, path), 403, (method, path))

    def test_its_jobs_are_allowed_and_share_the_oracle_allowance(self):
        from app import jobs
        for task in ("run_auto_mail_job", "run_preview_job"):
            name = f"app.services.sales_register.auto_mail:{task}"
            self.assertIn(name, jobs._ALLOWED_TASKS)
            self.assertEqual(jobs._RESOURCE_BY_TASK[name], "oracle-gst")


class _Db(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[
            User.__table__, EmailSettings.__table__, ScheduledReportMail.__table__, SystemEmailSettings.__table__])
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)

    def person(self, email, first, sender=None, password=None):
        user = User(email=email, first_name=first, last_name="X", password_hash="x", role="admin",
                    is_active=True, is_deleted=False)
        self.db.add(user)
        self.db.commit()
        self.db.add(EmailSettings(user_id=user.id, sender_email=sender, is_deleted=False,
                                  app_password_encrypted=security.encrypt(password) if password else None))
        self.db.commit()
        return user

    def system(self, email="no-reply@rdc.in", password="sys-pass"):
        self.db.add(SystemEmailSettings(id=1, sender_email=email, app_password_encrypted=security.encrypt(password),
                                        is_deleted=False))
        self.db.commit()


class SenderChoiceTests(_Db):
    def test_the_options_are_me_the_system_email_and_other_people(self):
        me = self.person("me@rdc.in", "Meera", "meera@gmail.com", "pw1")
        self.person("asha@rdc.in", "Asha", "asha@gmail.com", "pw2")
        self.person("nomail@rdc.in", "Ben")
        self.system()
        options = mail.manual_sender_options(self.db, me)
        self.assertEqual([(o["value"], o["email"], o["configured"]) for o in options],
                         [("me", "meera@gmail.com", True), ("system", "no-reply@rdc.in", True),
                          ("noreply", "no-reply@rdc.in", False),
                          (str(options[3]["value"]), "asha@gmail.com", True)])

    def no_reply(self, password="nr-pass", email="no-reply@rdc.in"):
        self.db.add(SystemEmailSettings(id=2, sender_email=email, app_password_encrypted=security.encrypt(password),
                                        is_deleted=False))
        self.db.commit()

    def test_the_no_reply_address_is_always_offered_and_works_once_it_has_a_password(self):
        me = self.person("me@rdc.in", "Meera", "meera@gmail.com", "pw1")
        self.system(email="boss@rdc.in")                     # the system email is someone else's address
        before = {o["value"]: o for o in mail.manual_sender_options(self.db, me)}
        self.assertEqual(before["noreply"]["email"], "no-reply@rdc.in")      # shown even before it is set up
        self.assertFalse(before["noreply"]["configured"])
        self.assertFalse(mail.resolve_manual_sender(self.db, "noreply", me.id)["configured"])
        self.assertIn("app password", mail.resolve_manual_sender(self.db, "noreply", me.id)["problem"])
        self.no_reply()
        after = mail.resolve_manual_sender(self.db, "noreply", me.id)
        self.assertEqual((after["email"], after["app_password"], after["configured"]),
                         ("no-reply@rdc.in", "nr-pass", True))
        self.assertEqual(mail.resolve_sender(self.db, mail.NO_REPLY_SENDER)["email"], "no-reply@rdc.in")
        self.assertEqual(mail.resolve_sender(self.db, None)["email"], "boss@rdc.in")   # untouched

    def test_an_automatic_mail_with_a_choosable_sender_can_be_saved_to_go_from_the_no_reply_address(self):
        key = "gst-invoice-adder"      # the Sales Register itself is fixed to the system email
        data = {"enabled": False, "days": [0], "time": "08:30", "to": [], "cc": [],
                "subject_template": "S {date}", "intro_template": "Hi {date}"}
        with patch.object(mail, "set_report_recipient_defaults"),                 patch.object(mail, "get_report_recipient_defaults", lambda db, key: {"to": [], "cc": []}),                 patch.object(mail.audit_middleware, "log_event"):
            with self.assertRaises(ValueError) as refused:
                mail.save_settings(self.db, key, {**data, "sender_user_id": mail.NO_REPLY_SENDER})
            self.assertIn("no-reply", str(refused.exception))
            self.no_reply()
            view = mail.save_settings(self.db, key, {**data, "sender_user_id": mail.NO_REPLY_SENDER})
        self.assertEqual(view["sender_user_id"], mail.NO_REPLY_SENDER)
        self.assertEqual(view["sender_email"], "no-reply@rdc.in")
        self.assertTrue(view["sender_configured"])
        self.assertTrue(view["no_reply_configured"])

    def test_each_choice_resolves_to_its_own_account(self):
        me = self.person("me@rdc.in", "Meera", "meera@gmail.com", "pw1")
        asha = self.person("asha@rdc.in", "Asha", "asha@gmail.com", "pw2")
        self.system()
        got = {c: mail.resolve_manual_sender(self.db, c, me.id)
               for c in (None, "me", "system", str(asha.id), "garbage", "999")}
        self.assertEqual((got[None]["email"], got["me"]["email"]), ("meera@gmail.com", "meera@gmail.com"))
        self.assertEqual((got["system"]["email"], got["system"]["app_password"]), ("no-reply@rdc.in", "sys-pass"))
        self.assertEqual((got[str(asha.id)]["email"], got[str(asha.id)]["app_password"]), ("asha@gmail.com", "pw2"))
        self.assertFalse(got["garbage"]["configured"])
        self.assertFalse(got["999"]["configured"])

    def test_a_system_email_that_is_not_set_up_cannot_send(self):
        me = self.person("me@rdc.in", "Meera", "meera@gmail.com", "pw1")
        sender = mail.resolve_manual_sender(self.db, "system", me.id)
        self.assertFalse(sender["configured"])
        self.assertIn("not set up", sender["problem"])

    def test_the_preview_records_who_it_is_from_and_the_confirm_uses_that(self):
        from app.services import report_mail_preview
        me = self.person("me@rdc.in", "Meera", "meera@gmail.com", "pw1")
        self.system()

        def build(snapshot, progress_cb=None, cancel_event=None):
            return mail.MailPayload(values={"date": "d"})

        with patch.object(report_mail_preview, "SessionLocal", sessionmaker(bind=self.db.get_bind())):
            result = report_mail_preview.build_preview(
                "gst-invoice-adder", build, me.id, ["a@b.in"], [], "S", "Hi {date}", sender="system")
            own = report_mail_preview.build_preview(
                "gst-invoice-adder", build, me.id, ["a@b.in"], [], "S", "Hi {date}", sender="me")
        self.assertEqual((result["status"], result["from_email"], result["sender"]), ("preview", "no-reply@rdc.in", "system"))
        self.assertEqual((own["from_email"], own["sender"]), ("meera@gmail.com", "me"))


class ScheduleTests(unittest.TestCase):
    def row(self, **kw):
        base = dict(enabled=True, is_deleted=False, send_mode="weekly", send_days="0,1,2,3,4,5", send_time="08:30",
                    send_times=None, send_month_days=None, last_attempt_date=None, last_attempt_slot=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def at(self, text):
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=IST)

    def test_weekly_with_one_time_behaves_as_before(self):
        row = self.row()
        self.assertTrue(mail.due_now(row, self.at("2026-10-30 08:30")))           # Friday
        self.assertFalse(mail.due_now(row, self.at("2026-10-30 08:29")))
        self.assertFalse(mail.due_now(row, self.at("2026-11-01 09:00")))          # Sunday
        row.last_attempt_date = date(2026, 10, 30)
        self.assertFalse(mail.due_now(row, self.at("2026-10-30 12:00")))
        self.assertEqual(mail.next_run(row, self.at("2026-10-30 12:00")), self.at("2026-10-31 08:30"))

    def test_several_times_a_day_each_go_out_once(self):
        row = self.row(send_time="08:30", send_times="08:30,14:00,18:15")
        self.assertEqual(mail.due_slot(row, self.at("2026-10-30 08:40")), "2026-10-30 08:30")
        row.last_attempt_slot = "2026-10-30 08:30"
        self.assertIsNone(mail.due_slot(row, self.at("2026-10-30 13:59")))
        self.assertEqual(mail.next_run(row, self.at("2026-10-30 13:00")), self.at("2026-10-30 14:00"))
        self.assertEqual(mail.due_slot(row, self.at("2026-10-30 14:00")), "2026-10-30 14:00")
        row.last_attempt_slot = "2026-10-30 18:15"
        self.assertIsNone(mail.due_slot(row, self.at("2026-10-30 23:00")))
        self.assertEqual(mail.next_run(row, self.at("2026-10-30 23:00")), self.at("2026-10-31 08:30"))

    def test_after_downtime_the_mail_goes_out_once_not_once_per_missed_slot(self):
        row = self.row(send_times="08:30,14:00,18:15")
        self.assertEqual(mail.due_slot(row, self.at("2026-10-30 19:00")), "2026-10-30 18:15")
        row.last_attempt_slot = "2026-10-30 18:15"
        self.assertIsNone(mail.due_slot(row, self.at("2026-10-30 19:05")))

    def test_monthly_on_chosen_days_and_on_the_last_day(self):
        row = self.row(send_mode="monthly", send_month_days="1,15,0")
        due = [d for d in (date(2026, 11, 1) + timedelta(days=i) for i in range(30))
               if mail.runs_on(row, d)]
        self.assertEqual(due, [date(2026, 11, 1), date(2026, 11, 15), date(2026, 11, 30)])
        self.assertTrue(mail.runs_on(row, date(2028, 2, 29)))        # last day of a leap February
        self.assertFalse(mail.runs_on(row, date(2028, 2, 28)))
        self.assertEqual(mail.next_run(row, self.at("2026-11-02 09:00")), self.at("2026-11-15 08:30"))

    def test_day_31_only_in_the_months_that_have_it(self):
        row = self.row(send_mode="monthly", send_month_days="31")
        self.assertTrue(mail.runs_on(row, date(2026, 10, 31)))
        self.assertFalse(mail.runs_on(row, date(2026, 11, 30)))
        self.assertEqual(mail.next_run(row, self.at("2026-11-02 09:00")), self.at("2026-12-31 08:30"))

    def every(self, **kw):
        """A report with an every-4-hours schedule (first mail 09:00) as well as its days / times."""
        return self.row(send_every_hours=4, send_every_start="09:00", last_every_slot="2026-10-30 09:00",
                        last_attempt_slot="2026-10-30 08:30", **kw)

    def test_every_few_hours_goes_out_round_the_clock_including_across_midnight(self):
        row = self.every()
        self.assertIsNone(mail.due_every_slot(row, self.at("2026-10-30 12:59")))
        self.assertEqual(mail.due_every_slot(row, self.at("2026-10-30 13:00")), "2026-10-30 13:00")
        row.last_every_slot = "2026-10-30 21:00"
        self.assertIsNone(mail.due_every_slot(row, self.at("2026-10-31 00:59")))
        self.assertEqual(mail.due_every_slot(row, self.at("2026-10-31 01:00")), "2026-10-31 01:00")

    def test_every_few_hours_ignores_the_days_so_a_sunday_runs_too(self):
        row = self.every(send_days="0")                                           # the days say Monday only
        row.last_every_slot = "2026-11-01 05:00"                                  # a Sunday
        self.assertEqual(mail.due_every_slot(row, self.at("2026-11-01 09:00")), "2026-11-01 09:00")
        self.assertIsNone(mail.due_slot(row, self.at("2026-11-01 08:30")))        # the days / times one does not

    def test_every_few_hours_after_downtime_goes_out_once_on_the_grid(self):
        row = self.every()
        self.assertEqual(mail.due_every_slot(row, self.at("2026-10-30 23:30")), "2026-10-30 21:00")
        row.last_every_slot = "2026-10-30 21:00"
        self.assertIsNone(mail.due_every_slot(row, self.at("2026-10-30 23:35")))

    def test_the_daily_schedule_and_the_every_few_hours_one_run_side_by_side(self):
        row = self.every(send_days="0,1,2,3,4,5,6", send_time="08:30")
        row.last_attempt_slot = "2026-10-30 08:30"
        now = self.at("2026-10-31 08:30")                                         # the daily slot, not an hourly one
        self.assertEqual(mail.due_slot(row, now), "2026-10-31 08:30")
        self.assertIsNone(mail.due_every_slot(row, self.at("2026-10-30 12:00")))
        # next_run is the sooner of the two: the every-4-hours slot at 13:00 beats tomorrow's 08:30
        self.assertEqual(mail.next_run(row, self.at("2026-10-30 10:00")), self.at("2026-10-30 13:00"))
        row.last_every_slot = "2026-10-30 21:00"                                  # now the daily one is sooner
        self.assertEqual(mail.next_run(row, self.at("2026-10-30 22:00")), self.at("2026-10-31 01:00"))
        row.send_every_hours = 23
        self.assertEqual(mail.next_run(row, self.at("2026-10-30 22:00")), self.at("2026-10-31 08:30"))

    def test_the_days_and_times_can_be_off_leaving_only_every_few_hours(self):
        row = self.every(send_times_enabled=False, send_days="0,1,2,3,4,5,6")
        self.assertIsNone(mail.due_slot(row, self.at("2026-10-31 08:30")))
        self.assertEqual(mail.next_run(row, self.at("2026-10-30 10:00")), self.at("2026-10-30 13:00"))
        self.assertTrue(mail.due_now(row, self.at("2026-10-30 13:00")))
        self.assertFalse(mail.due_now(row, self.at("2026-10-30 12:00")))

    def test_every_few_hours_is_off_when_switched_off_or_with_a_bad_number_of_hours(self):
        self.assertIsNone(mail.due_every_slot(self.every(enabled=False), self.at("2026-10-31 09:00")))
        row = self.every()
        row.send_every_hours = 1
        self.assertIsNone(mail.every_hours(row))
        self.assertIsNone(mail.due_every_slot(row, self.at("2026-10-31 09:00")))

    def test_the_every_few_hours_anchor_counts_from_the_start_time_without_firing_anything(self):
        anchor = mail.hourly_anchor
        self.assertEqual(anchor("09:00", 4, self.at("2026-10-30 07:00")), "2026-10-30 05:00")   # next: 09:00
        self.assertEqual(anchor("09:00", 4, self.at("2026-10-30 09:00")), "2026-10-30 09:00")
        self.assertEqual(anchor("09:00", 4, self.at("2026-10-30 14:30")), "2026-10-30 13:00")   # next: 17:00
        self.assertEqual(anchor("09:00", 23, self.at("2026-10-30 23:59")), "2026-10-30 09:00")

    def test_switched_off_or_no_days_is_never_due(self):
        self.assertIsNone(mail.next_run(self.row(enabled=False)))
        self.assertFalse(mail.due_now(self.row(enabled=False), self.at("2026-10-30 09:00")))
        self.assertIsNone(mail.next_run(self.row(send_days="")))


class SaveScheduleTests(_Db):
    DATA = {"enabled": True, "mode": "weekly", "days": [0, 4], "month_days": [], "time": "08:30",
            "times": ["14:00", "08:30"], "to": ["a@rdc.in"], "cc": [], "subject_template": "S {date}",
            "intro_template": "Hi {date}"}

    def save(self, **changes):
        with patch.object(mail, "set_report_recipient_defaults"), \
                patch.object(mail, "get_report_recipient_defaults", lambda db, key: {"to": ["a@rdc.in"], "cc": []}), \
                patch.object(mail.audit_middleware, "log_event"), \
                patch.object(mail, "now_ist", lambda: datetime(2026, 10, 30, 7, 0, tzinfo=IST)):
            return mail.save_settings(self.db, "gst-invoice-adder", {**self.DATA, **changes})

    def test_weekly_with_several_times_is_saved_and_read_back(self):
        view = self.save()
        self.assertEqual((view["mode"], view["days"], view["times"], view["time"]),
                         ("weekly", [0, 4], ["08:30", "14:00"], "08:30"))

    def test_monthly_days_including_the_last_day_are_saved(self):
        view = self.save(mode="monthly", month_days=[15, 1, 0])
        self.assertEqual((view["mode"], view["month_days"]), ("monthly", [0, 1, 15]))

    def test_a_schedule_with_nothing_to_run_on_is_refused_unless_switched_off(self):
        with self.assertRaises(ValueError):
            self.save(mode="monthly", month_days=[])
        with self.assertRaises(ValueError):
            self.save(days=[])
        self.assertFalse(self.save(days=[], enabled=False)["enabled"])

    def test_bad_times_and_too_many_times_are_refused(self):
        with self.assertRaises(ValueError):
            self.save(times=["25:00"])
        with self.assertRaises(ValueError):
            self.save(times=[f"0{i}:00" for i in range(7)])
        with self.assertRaises(ValueError):
            self.save(mode="yearly")

    def save_sales_register(self, **changes):
        with patch.object(mail, "set_report_recipient_defaults"), \
                patch.object(mail, "get_report_recipient_defaults", lambda db, key: {"to": ["a@rdc.in"], "cc": []}), \
                patch.object(mail.audit_middleware, "log_event"), \
                patch.object(mail, "now_ist", lambda: datetime(2026, 10, 30, 7, 0, tzinfo=IST)):
            return mail.save_settings(self.db, auto_mail.APP_KEY, {**self.DATA, **changes})

    def test_every_few_hours_is_saved_next_to_the_daily_schedule_and_starts_at_its_time(self):
        view = self.save_sales_register(every_hours=6, every_start="08:30")
        self.assertEqual((view["every_hours"], view["every_start"], view["allow_hourly"], view["times_enabled"]),
                         (6, "08:30", True, True))
        self.assertEqual((view["mode"], view["days"], view["times"]), ("weekly", [0, 4], ["08:30", "14:00"]))
        row = mail.get_or_create_settings(self.db, auto_mail.APP_KEY)
        self.assertEqual(row.last_every_slot, "2026-10-30 02:30")                  # next: 08:30
        self.assertEqual(mail.next_run(row, datetime(2026, 10, 30, 7, 0, tzinfo=IST)),
                         datetime(2026, 10, 30, 8, 30, tzinfo=IST))
        self.assertEqual(mail.due_every_slot(row, datetime(2026, 10, 30, 8, 30, tzinfo=IST)), "2026-10-30 08:30")

    def test_every_few_hours_can_be_the_only_schedule(self):
        view = self.save_sales_register(times_enabled=False, days=[], every_hours=3, every_start="09:00")
        self.assertEqual((view["times_enabled"], view["every_hours"]), (False, 3))
        with self.assertRaises(ValueError):                      # nothing left to send on
            self.save_sales_register(times_enabled=False, every_hours=None)
        self.assertFalse(self.save_sales_register(times_enabled=False, every_hours=None, enabled=False)["enabled"])

    def test_every_n_hours_is_limited_to_2_through_23_and_needs_a_start_time(self):
        for bad in (1, 0, 24, 5.5, "4", True):
            with self.assertRaises(ValueError):
                self.save_sales_register(every_hours=bad, every_start="09:00")
        with self.assertRaises(ValueError):
            self.save_sales_register(every_hours=4, every_start="")
        with self.assertRaises(ValueError):
            self.save_sales_register(every_hours=4, every_start="25:00")
        for good in (2, 23):
            self.assertEqual(self.save_sales_register(every_hours=good, every_start="09:00")["every_hours"], good)
        view = self.save_sales_register(every_hours=None)
        self.assertEqual((view["every_hours"], view["every_start"]), (None, None))

    def test_the_sales_register_sender_is_always_the_system_email(self):
        self.person("asha@rdc.in", "Asha", "asha@gmail.com", "pw")
        self.db.add(SystemEmailSettings(id=2, sender_email="no-reply@rdc.in", is_deleted=False,
                                        app_password_encrypted=security.encrypt("nr")))
        self.db.commit()
        for choice in (mail.NO_REPLY_SENDER, 1, None):
            view = self.save_sales_register(sender_user_id=choice)
            self.assertIsNone(view["sender_user_id"])
            self.assertTrue(view["sender_fixed"])
            self.assertEqual(view["sender_options"], [])
        # one that was chosen before the sender was fixed is cleared, and a scheduled run ignores it
        row = mail.get_or_create_settings(self.db, auto_mail.APP_KEY)
        row.sender_user_id = mail.NO_REPLY_SENDER
        self.db.commit()
        with patch.object(mail, "get_report_recipient_defaults", lambda db, key: {"to": [], "cc": []}):
            self.assertIsNone(mail.settings_view(self.db, auto_mail.APP_KEY)["sender_user_id"])

    def test_other_reports_still_let_the_sender_be_chosen(self):
        self.assertFalse(mail.SPECS["gst-invoice-adder"].fixed_sender)
        self.assertFalse(self.save()["sender_fixed"])

    def test_every_automatic_mail_offers_every_few_hours(self):
        for key, spec in mail.SPECS.items():
            self.assertEqual(spec.allow_hourly, spec.scheduled, key)      # only a mail that is never scheduled lacks it
        view = self.save(every_hours=4, every_start="09:00")
        self.assertEqual((view["allow_hourly"], view["every_hours"], view["every_start"]), (True, 4, "09:00"))
        self.assertEqual(view["days"], self.DATA["days"])                  # next to the days / times, not instead
        with self.assertRaises(ValueError):
            self.save(every_hours=1, every_start="09:00")
        with self.assertRaises(ValueError):
            self.save(mode="hourly")

    def test_saving_after_a_slot_has_passed_does_not_fire_it_but_a_later_slot_still_will(self):
        with patch.object(mail, "set_report_recipient_defaults"), \
                patch.object(mail, "get_report_recipient_defaults", lambda db, key: {"to": ["a@rdc.in"], "cc": []}), \
                patch.object(mail.audit_middleware, "log_event"), \
                patch.object(mail, "now_ist", lambda: datetime(2026, 10, 30, 10, 0, tzinfo=IST)):
            mail.save_settings(self.db, "gst-invoice-adder", self.DATA)
        row = mail.get_or_create_settings(self.db, "gst-invoice-adder")
        self.assertEqual(row.last_attempt_slot, "2026-10-30 08:30")
        self.assertIsNone(mail.due_slot(row, datetime(2026, 10, 30, 11, 0, tzinfo=IST)))
        self.assertEqual(mail.due_slot(row, datetime(2026, 10, 30, 14, 1, tzinfo=IST)), "2026-10-30 14:00")


if __name__ == "__main__":
    unittest.main()
