import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from app.services.invoice_booking_tracker import monitor
from app.routers.invoice_booking_tracker import _visible_error


class InvoiceBookingTrackerTests(unittest.TestCase):
    def test_booked_comparison_is_case_insensitive_and_trimmed(self):
        statuses = ["BOOKED", "Booked", " booked ", "bOoKeD", "Pending", "", "In Review"]
        self.assertEqual(monitor.count_unbooked(statuses), 3)

    def test_rendered_mail_contains_complete_table_and_computed_total(self):
        rows = [
            {"location": "HO", "responsible_person": "Hitanshi", "pending": 11},
            {"location": "WADA", "responsible_person": "Vishal", "pending": 0},
        ]
        subject, body = monitor.render_templates(
            "Tracker as on {date} — {total_pending}",
            "Hello\n\n{tracker_table}\n\nLocations: {location_count}",
            rows,
            date(2026, 9, 2),
        )
        self.assertEqual(subject, "Tracker as on 2nd September 2026 — 11")
        self.assertIn("<table", body)
        self.assertIn("Grand Total", body)
        self.assertIn(">11</", body)
        self.assertIn("WADA", body)
        self.assertIn("Locations: 2", body)

    def test_subject_rejects_html_table_placeholder(self):
        with self.assertRaisesRegex(ValueError, "tracker_table"):
            monitor.validate_template("{tracker_table}", allow_table=False)

    def test_workbook_matches_tracker_shape_and_uses_total_formula(self):
        rows = [
            {"location": "HO", "responsible_person": "Hitanshi", "pending": 11},
            {"location": "WADA", "responsible_person": "Vishal", "pending": 0},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracker.xlsx"
            monitor.create_workbook(rows, path, date(2026, 9, 2))
            workbook = load_workbook(path, data_only=False)
            sheet = workbook["Invoice Booking Tracker"]
            self.assertEqual(sheet["A1"].value, "UF PENDING INVOICE BOOKING TRACKER AS ON 02-09-2026")
            self.assertEqual(sheet["A2"].value, "Locations")
            self.assertEqual(sheet["C3"].value, 11)
            self.assertEqual(sheet["C4"].number_format, "0;-0;-")
            self.assertEqual(sheet["C5"].value, "=SUM(C3:C4)")
            self.assertEqual(sheet.freeze_panes, "A3")

    def test_three_attempt_retry_discards_saved_session_after_first_failure(self):
        snapshot = {
            "login_url": "https://example.invalid/",
            "username": "user",
            "password": "secret",
            "saved_session": {"cookies": [{"name": "old"}]},
            "login_timeout_seconds": 30,
        }
        calls = []

        def fake_fetch(current, mappings):
            calls.append(current.get("saved_session"))
            if len(calls) < 3:
                raise RuntimeError("temporary portal failure")
            return [{**mappings[0], "pending": 1, "records_scanned": 2, "pages_scanned": 1}], {"cookies": []}

        with patch.object(monitor, "fetch_tracker", side_effect=fake_fetch):
            rows, session, attempts = monitor.fetch_tracker_with_retries(snapshot, [{"location": "HO"}])

        self.assertEqual(attempts, 3)
        self.assertIsNotNone(calls[0])
        self.assertIsNone(calls[1])
        self.assertIsNone(calls[2])
        self.assertEqual(rows[0]["pending"], 1)
        self.assertEqual(session, {"cookies": []})

    def test_retry_stops_after_exactly_three_failures(self):
        snapshot = {"saved_session": None}
        with patch.object(monitor, "fetch_tracker", side_effect=RuntimeError("down")) as fetch:
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                monitor.fetch_tracker_with_retries(snapshot, [])
        self.assertEqual(fetch.call_count, 3)

    def test_regular_users_receive_specific_account_in_use_state_only(self):
        technical = f"{monitor.ACCOUNT_IN_USE_ERROR_PREFIX} portal detail"
        self.assertEqual(
            _visible_error(technical, False),
            monitor.ACCOUNT_IN_USE_PUBLIC_MESSAGE,
        )
        self.assertEqual(_visible_error("unexpected parser detail", False), "We have encountered an issue, please contact Jarjish 🥲")
        self.assertEqual(_visible_error(technical, True), technical)


if __name__ == "__main__":
    unittest.main()
