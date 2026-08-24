import unittest
from datetime import datetime, timezone

from app.regional import format_indian_number, to_ist, to_ist_iso
from app.services.erp_converter.value_parser import parse_value


class IndianRegionalConventionTests(unittest.TestCase):
    def test_utc_timestamp_converts_to_ist(self):
        utc_value = datetime(2026, 8, 21, 18, 45, tzinfo=timezone.utc)

        converted = to_ist(utc_value)

        self.assertEqual(converted.strftime("%Y-%m-%d %H:%M"), "2026-08-22 00:15")
        self.assertEqual(to_ist_iso(utc_value), "2026-08-22T00:15:00+05:30")

    def test_naive_database_timestamp_is_treated_as_utc(self):
        converted = to_ist(datetime(2026, 1, 1, 0, 0))

        self.assertEqual(converted.strftime("%Y-%m-%d %H:%M"), "2026-01-01 05:30")

    def test_indian_lakh_and_crore_grouping(self):
        self.assertEqual(format_indian_number(1234), "1,234")
        self.assertEqual(format_indian_number(123456), "1,23,456")
        self.assertEqual(format_indian_number(12345678), "1,23,45,678")
        self.assertEqual(format_indian_number(-123456.5), "-1,23,456.50")

    def test_erp_converter_emits_indian_excel_number_formats(self):
        self.assertEqual(parse_value("1,234.50"), (1234.5, "##,##,##0.00"))
        self.assertEqual(parse_value("1,23,456.50"), (123456.5, "##,##,##0.00"))


if __name__ == "__main__":
    unittest.main()
