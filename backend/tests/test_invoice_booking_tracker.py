import unittest
import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models import InvoiceBookingTrackerCheck
from app.services.invoice_booking_tracker import monitor
from app.routers.invoice_booking_tracker import _visible_error, latest_tracker


@compiles(LONGTEXT, "sqlite")
def _longtext_as_text_on_sqlite(element, compiler, **kw):
    return "TEXT"


class InvoiceBookingTrackerTests(unittest.TestCase):
    def test_bundled_queue_keys_match_live_dms_routes(self):
        by_location = {location: (label, key) for location, _, label, key in monitor.DEFAULT_MAPPINGS}
        self.assertEqual(by_location["ANDHRA/Nellore"][1], "ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_ANDHRA")
        self.assertEqual(by_location["FlyAsh"][1], "ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_FLYASH_TRADING")
        self.assertEqual(by_location["HO"][1], "ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_HEAD_OFFICE")
        self.assertEqual(by_location["TELANGANA"][1], "ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA")
        self.assertEqual(by_location["VIZAG/VISAKHAPATNAM"][1], "ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_VISAKHAPATNAM")

    def test_status_classification_is_case_insensitive_and_counts_only_the_two_target_statuses(self):
        statuses = [
            "Pending for Approval",
            " pending for approval ",
            "PENDING FOR APPROVAL",
            "Submitted to Accounts",
            "SUBMITTED TO ACCOUNTS",
            "BOOKED",
            "Booked",
            "Rejected",
            "In Review",
            "",
        ]
        self.assertEqual(monitor.classify_statuses(statuses), (3, 2))

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
        self.assertIn("UF PENDING INVOICE BOOKING TRACKER AS ON 02-09-2026", body)
        self.assertIn("Grand Total", body)
        self.assertIn(">11<", body)
        self.assertIn(">-<", body)  # WADA's zero pending renders as a dash
        self.assertIn("WADA", body)
        self.assertIn("Locations: 2", body)
        self.assertNotIn("<p style=", body)  # no signature configured -> nothing appended

    def test_rendered_mail_appends_signature_when_configured(self):
        rows = [{"location": "HO", "responsible_person": "Hitanshi", "pending": 1}]
        _, body = monitor.render_templates(
            "Tracker as on {date}", "{tracker_table}", rows, date(2026, 9, 2), signature="Regards,\nAccounts Team",
        )
        self.assertIn("Regards,<br>Accounts Team", body)

    def test_rendered_mail_omits_signature_block_when_blank(self):
        rows = [{"location": "HO", "responsible_person": "Hitanshi", "pending": 1}]
        _, body = monitor.render_templates("Tracker as on {date}", "{tracker_table}", rows, date(2026, 9, 2), signature="   ")
        self.assertNotIn("<p style=", body)

    def test_subject_rejects_html_table_placeholder(self):
        with self.assertRaisesRegex(ValueError, "tracker_table"):
            monitor.validate_template("{tracker_table}", allow_table=False)

    def test_table_html_matches_original_tracker_shape_and_colors(self):
        """Reproduces the original, proven manual tracker's exact look: a
        salmon title banner, a "Total Pending" third column, and a peach
        grand-total row - the breakdown by status lives only in the web
        app's own tables, not this mail-embedded table."""
        rows = [
            {"location": "HO", "responsible_person": "Hitanshi", "pending_for_approval": 7, "submitted_to_accounts": 4, "pending": 11},
            {"location": "WADA", "responsible_person": "Vishal", "pending_for_approval": 0, "submitted_to_accounts": 0, "pending": 0},
        ]
        body = monitor._table_html(rows, date(2026, 9, 2))
        self.assertIn("UF PENDING INVOICE BOOKING TRACKER AS ON 02-09-2026", body)
        self.assertIn("background:#F4B183", body)
        self.assertIn("background:#FBE5D6", body)
        self.assertIn(">Total Pending<", body)
        self.assertIn(">11<", body)
        self.assertIn(">-<", body)
        self.assertNotIn("Pending for Approval", body)
        self.assertNotIn("Submitted to Accounts", body)

    def test_three_attempt_retry_discards_saved_session_after_first_failure(self):
        snapshot = {
            "login_url": "https://example.invalid/",
            "username": "user",
            "password": "secret",
            "saved_session": {"cookies": [{"name": "old"}]},
            "login_timeout_seconds": 30,
        }
        calls = []

        def fake_fetch(current, mappings, heartbeat=None):
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

    def test_finds_accounting_status_column_and_ignores_the_similarly_named_status_and_workflow_status_columns(self):
        """The live table carries three status-like columns: a coarse
        "Status" (Approved/Booked), "Accounting Status" (Pending for
        Approval / Submitted to Accounts / Booked / Rejected - the values
        the tracker classifies), and "Workflow Status" (Rejected/etc. on a
        separate axis). Matching the wrong one silently zeroes every count."""
        class Headers:
            def __init__(self, values): self.values = values
            def all_inner_texts(self): return self.values
        class Table:
            def __init__(self, headers): self._headers = headers
            def locator(self, selector):
                assert "thead th" in selector
                return Headers(self._headers)
        class Tables:
            def __init__(self, tables): self.tables = tables
            def count(self): return len(self.tables)
            def nth(self, index): return self.tables[index]

        table = Table(["PO Number", "MRN Number", "Status", "Accounting Status", "Payment By", "Workflow Status"])
        page = SimpleNamespace(locator=lambda selector: Tables([table]))

        found = monitor._find_status_table(page)

        self.assertIsNotNone(found)
        found_table, index = found
        self.assertIs(found_table, table)
        self.assertEqual(index, 3)

    def test_no_accounting_status_column_is_not_mistaken_for_the_generic_status_column(self):
        class Headers:
            def __init__(self, values): self.values = values
            def all_inner_texts(self): return self.values
        class Table:
            def __init__(self, headers): self._headers = headers
            def locator(self, selector): return Headers(self._headers)
        class Tables:
            def __init__(self, tables): self.tables = tables
            def count(self): return len(self.tables)
            def nth(self, index): return self.tables[index]

        table = Table(["Status", "Workflow Status"])
        page = SimpleNamespace(locator=lambda selector: Tables([table]))

        self.assertIsNone(monitor._find_status_table(page))

    def test_status_table_waits_for_async_datatables_header(self):
        page = SimpleNamespace(wait_for_timeout=lambda milliseconds: None)
        table = object()
        with patch.object(monitor, "_find_status_table", side_effect=[None, None, (table, 4)]) as finder:
            found, status_index = monitor._status_table(page, timeout_seconds=1)
        self.assertIs(found, table)
        self.assertEqual(status_index, 4)
        self.assertEqual(finder.call_count, 3)

    def test_status_table_zero_timeout_is_an_immediate_probe(self):
        page = SimpleNamespace(wait_for_timeout=lambda milliseconds: self.fail("must not wait"))
        with patch.object(monitor, "_find_status_table", return_value=None), patch.object(monitor, "_queue_has_no_results", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Status column"):
                monitor._status_table(page, timeout_seconds=0)

    def test_zero_workflow_case_page_is_a_successful_empty_queue(self):
        page = SimpleNamespace(
            goto=lambda *args, **kwargs: None,
            wait_for_timeout=lambda milliseconds: None,
        )
        with patch.object(monitor, "_find_status_table", return_value=None), patch.object(monitor, "_queue_has_no_results", return_value=True):
            self.assertEqual(monitor._scan_queue(page, "https://example.invalid/queue"), (0, 0, 0, 1))

    def test_page_size_uses_largest_finite_datatables_option(self):
        class Option:
            def __init__(self, value): self.value = value
            def get_attribute(self, name): return self.value if name == "value" else None
            def inner_text(self): return self.value
        class Options:
            values = ["10", "25", "100", "-1"]
            def count(self): return len(self.values)
            def nth(self, index): return Option(self.values[index])
        class Select:
            selected = None
            def locator(self, selector): return Options()
            def input_value(self): return "10"
            def select_option(self, *, value): self.selected = value
        select = Select()
        selectors = SimpleNamespace(count=lambda: 1, first=select)
        info = SimpleNamespace(count=lambda: 1, first=SimpleNamespace(inner_text=lambda: "Showing 1 to 100 of 328 entries"))
        rows = SimpleNamespace(count=lambda: 100)
        waits = []
        def locator(selector):
            if "dataTables_length" in selector: return selectors
            if "dataTables_info" in selector: return info
            if selector == "table:visible tbody tr:visible": return rows
            raise AssertionError(selector)
        page = SimpleNamespace(locator=locator, wait_for_timeout=lambda milliseconds: waits.append(milliseconds))

        table = SimpleNamespace(locator=lambda selector: rows)
        with patch.object(monitor, "_find_status_table", return_value=(table, 4)):
            self.assertTrue(monitor._maximize_page_size(page))
        self.assertEqual(select.selected, "100")
        self.assertEqual(waits, [])

    def test_queue_discovery_prefers_exact_q_key_over_similar_label(self):
        class Link:
            def __init__(self, text, href): self.text, self.href = text, href
            def inner_text(self): return self.text
            def get_attribute(self, name): return self.href if name == "href" else None
        class Links:
            values = [
                Link("Accounts payment ultrafine invoices Telangana 12", "/console/workflowcases?Q=WRONG_SIMILAR_QUEUE"),
                Link("Portal spelling differs 34", "/console/workflowcases?Q=ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA"),
            ]
            def count(self): return len(self.values)
            def nth(self, index): return self.values[index]
        page = SimpleNamespace(
            url="https://example.invalid/console",
            goto=lambda *args, **kwargs: None,
            locator=lambda selector: Links(),
            wait_for_timeout=lambda milliseconds: None,
        )

        url = monitor._discover_queue_url(
            page,
            "https://example.invalid/",
            "Accounts payment ultrafine invoices Telangana",
            "ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA",
        )

        self.assertEqual(url, "https://example.invalid/console/workflowcases?Q=ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA")

    def test_queue_discovery_waits_for_late_exact_key_instead_of_early_fuzzy_link(self):
        class Link:
            def __init__(self, text, href): self.text, self.href = text, href
            def inner_text(self): return self.text
            def get_attribute(self, name): return self.href if name == "href" else None
        class Links:
            def __init__(self, values): self.values = values
            def count(self): return len(self.values)
            def nth(self, index): return self.values[index]
        calls = {"count": 0}
        def locator(_selector):
            calls["count"] += 1
            fuzzy = Link("Accounts payment ultrafine invoices Telangana 12", "/console/workflowcases?Q=WRONG_SIMILAR_QUEUE")
            exact = Link("Portal spelling differs 34", "/console/workflowcases?Q=ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA")
            return Links([fuzzy] if calls["count"] == 1 else [fuzzy, exact])
        page = SimpleNamespace(
            url="https://example.invalid/console",
            goto=lambda *args, **kwargs: None,
            locator=locator,
            wait_for_timeout=lambda milliseconds: None,
        )

        url = monitor._discover_queue_url(
            page,
            "https://example.invalid/",
            "Accounts payment ultrafine invoices Telangana",
            "ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA",
        )

        self.assertEqual(url, "https://example.invalid/console/workflowcases?Q=ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA")
        self.assertEqual(calls["count"], 2)

    def test_single_page_scan_counts_only_pending_for_approval_and_submitted_to_accounts(self):
        class CellLocator:
            def __init__(self, values): self.values = values
            def all_inner_texts(self): return self.values
        class Row:
            def __init__(self, values): self.values = values
            def locator(self, selector): return CellLocator(self.values)
        class Rows:
            def __init__(self, values): self.values = values
            def count(self): return len(self.values)
            def nth(self, index): return Row(self.values[index])
        class Table:
            def __init__(self, values): self.rows = Rows(values)
            def locator(self, selector): return self.rows
        class Empty:
            def count(self): return 0
        class Info:
            count = lambda self: 1
            first = SimpleNamespace(inner_text=lambda: "Showing 1 to 5 of 5 entries")
        table = Table([
            ["1", "BOOKED"],
            ["2", " Pending for Approval "],
            ["3", "SUBMITTED TO ACCOUNTS"],
            ["4", "Rejected"],
            ["5", ""],
        ])
        def locator(selector):
            if "dataTables_info" in selector: return Info()
            if "paginate_button.next" in selector: return Empty()
            raise AssertionError(selector)
        page = SimpleNamespace(goto=lambda *args, **kwargs: None, wait_for_timeout=lambda milliseconds: None, locator=locator)
        with patch.object(monitor, "_status_table", return_value=(table, 1)), patch.object(monitor, "_maximize_page_size", return_value=False):
            self.assertEqual(monitor._scan_queue(page, "https://example.invalid/queue"), (1, 1, 5, 1))

    def test_scan_rejects_a_partial_result_set(self):
        class CellLocator:
            def __init__(self, values): self.values = values
            def all_inner_texts(self): return self.values
        class Row:
            def __init__(self, values): self.values = values
            def locator(self, selector): return CellLocator(self.values)
        class Rows:
            values = [["1", "Booked"], ["2", "Approved"]]
            def count(self): return len(self.values)
            def nth(self, index): return Row(self.values[index])
        table = SimpleNamespace(locator=lambda selector: Rows())
        info = SimpleNamespace(count=lambda: 1, first=SimpleNamespace(inner_text=lambda: "Showing 1 to 2 of 3 entries"))
        empty = SimpleNamespace(count=lambda: 0)
        page = SimpleNamespace(
            goto=lambda *args, **kwargs: None,
            wait_for_timeout=lambda milliseconds: None,
            locator=lambda selector: info if "dataTables_info" in selector else empty,
        )
        with patch.object(monitor, "_status_table", return_value=(table, 1)), patch.object(monitor, "_maximize_page_size", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "scanned 2 of 3"):
                monitor._scan_queue(page, "https://example.invalid/queue")

    def test_fetch_tracker_always_logs_out_on_success_and_on_a_mid_scan_failure(self):
        """DMS permits only one active session per account. Every real browser
        run - whether every queue scans cleanly or one blows up mid-loop -
        must release that session before Chromium closes, or the next run
        (including the 08:00 schedule) finds the account already logged in."""

        class FakePage:
            def __init__(self):
                self.url = "https://example.invalid/console"

            def goto(self, *args, **kwargs):
                pass

        class FakeContext:
            def __init__(self):
                self.pages = [FakePage()]

            def new_page(self):
                return self.pages[0]

            def storage_state(self):
                return {"cookies": []}

        class FakeBrowser:
            def __init__(self):
                self.closed = False

            def new_context(self, **kwargs):
                return FakeContext()

            def close(self):
                self.closed = True

        class FakePlaywright:
            def __init__(self, browser):
                self.chromium = SimpleNamespace(launch=lambda **kwargs: browser)

        class FakeSyncPlaywright:
            def __init__(self, browser):
                self.browser = browser

            def __enter__(self):
                return FakePlaywright(self.browser)

            def __exit__(self, *exc_info):
                return False

        snapshot = {
            "login_url": "https://example.invalid/",
            "username": "user",
            "password": "secret",
            "saved_session": None,
            "login_timeout_seconds": 30,
        }
        mapping = {"location": "HO", "responsible_person": "Hitanshi", "queue_label": "Q", "queue_key": "K"}

        logout_calls = []
        browser = FakeBrowser()
        with patch("playwright.sync_api.sync_playwright", return_value=FakeSyncPlaywright(browser)), \
             patch.object(monitor, "_login", return_value=None), \
             patch.object(monitor, "_logout", side_effect=lambda page: (logout_calls.append(page), True)[1]), \
             patch.object(monitor, "_discover_queue_url", return_value="https://example.invalid/queue"), \
             patch.object(monitor, "_scan_queue", return_value=(1, 2, 3, 1)):
            monitor.fetch_tracker(snapshot, [mapping])
        self.assertEqual(len(logout_calls), 1)
        self.assertTrue(browser.closed)

        logout_calls.clear()
        browser = FakeBrowser()
        with patch("playwright.sync_api.sync_playwright", return_value=FakeSyncPlaywright(browser)), \
             patch.object(monitor, "_login", return_value=None), \
             patch.object(monitor, "_logout", side_effect=lambda page: (logout_calls.append(page), True)[1]), \
             patch.object(monitor, "_discover_queue_url", side_effect=RuntimeError("queue not found")):
            with self.assertRaises(RuntimeError):
                monitor.fetch_tracker(snapshot, [mapping])
        self.assertEqual(len(logout_calls), 1)
        self.assertTrue(browser.closed)

    def test_scan_queue_calls_heartbeat_once_per_scanned_page(self):
        """A worker process can die outright mid-scan (crash, OOM, service
        restart) - nothing can run cleanup at that point. Reserving the DB
        lock for the whole worst-case run duration up front left every other
        check "skipped" for up to ~72 minutes with no real work behind it.
        The heartbeat renews the lock in short windows instead, so a dead
        run's lock self-heals in minutes - but only if this is actually
        wired into the page-by-page scan loop, which is what this proves."""
        class CellLocator:
            def __init__(self, values): self.values = values
            def all_inner_texts(self): return self.values
        class Row:
            def __init__(self, values): self.values = values
            def locator(self, selector): return CellLocator(self.values)
        class Rows:
            def __init__(self, values): self.values = values
            def count(self): return len(self.values)
            def nth(self, index): return Row(self.values[index])
        class Table:
            def __init__(self, values): self.rows = Rows(values)
            def locator(self, selector): return self.rows
        class Empty:
            def count(self): return 0
        class Info:
            count = lambda self: 1
            first = SimpleNamespace(inner_text=lambda: "Showing 1 to 1 of 1 entries")
        table = Table([["1", "Pending for Approval"]])
        def locator(selector):
            if "dataTables_info" in selector: return Info()
            if "paginate_button.next" in selector: return Empty()
            raise AssertionError(selector)
        page = SimpleNamespace(goto=lambda *args, **kwargs: None, wait_for_timeout=lambda milliseconds: None, locator=locator)

        calls = []
        with patch.object(monitor, "_status_table", return_value=(table, 1)), patch.object(monitor, "_maximize_page_size", return_value=False):
            monitor._scan_queue(page, "https://example.invalid/queue", heartbeat=lambda: calls.append("page"))

        self.assertEqual(calls, ["page"])

    def test_fetch_tracker_calls_heartbeat_once_per_mapping(self):
        class FakePage:
            def __init__(self):
                self.url = "https://example.invalid/console"

            def goto(self, *args, **kwargs):
                pass

        class FakeContext:
            def __init__(self):
                self.pages = [FakePage()]

            def new_page(self):
                return self.pages[0]

            def storage_state(self):
                return {"cookies": []}

        class FakeBrowser:
            def new_context(self, **kwargs):
                return FakeContext()

            def close(self):
                pass

        class FakePlaywright:
            def __init__(self, browser):
                self.chromium = SimpleNamespace(launch=lambda **kwargs: browser)

        class FakeSyncPlaywright:
            def __init__(self, browser):
                self.browser = browser

            def __enter__(self):
                return FakePlaywright(self.browser)

            def __exit__(self, *exc_info):
                return False

        snapshot = {"login_url": "https://example.invalid/", "username": "u", "password": "p", "saved_session": None, "login_timeout_seconds": 30}
        mappings = [
            {"location": "HO", "responsible_person": "Hitanshi", "queue_label": "Q1", "queue_key": "K1"},
            {"location": "WADA", "responsible_person": "Vishal", "queue_label": "Q2", "queue_key": "K2"},
        ]
        calls = []
        with patch("playwright.sync_api.sync_playwright", return_value=FakeSyncPlaywright(FakeBrowser())), \
             patch.object(monitor, "_login", return_value=None), \
             patch.object(monitor, "_logout", return_value=True), \
             patch.object(monitor, "_discover_queue_url", return_value="https://example.invalid/queue"), \
             patch.object(monitor, "_scan_queue", return_value=(0, 0, 0, 1)):
            monitor.fetch_tracker(snapshot, mappings, heartbeat=lambda: calls.append("hb"))

        self.assertEqual(len(calls), 2)

    def test_fetch_tracker_with_retries_calls_heartbeat_before_each_attempt(self):
        calls = []
        with patch.object(monitor, "fetch_tracker", side_effect=RuntimeError("down")):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                monitor.fetch_tracker_with_retries({"saved_session": None}, [], heartbeat=lambda: calls.append("hb"))
        self.assertEqual(len(calls), monitor.CHECK_MAX_ATTEMPTS)

    def test_regular_users_receive_specific_account_in_use_state_only(self):
        technical = f"{monitor.ACCOUNT_IN_USE_ERROR_PREFIX} portal detail"
        self.assertEqual(
            _visible_error(technical, False),
            monitor.ACCOUNT_IN_USE_PUBLIC_MESSAGE,
        )
        self.assertEqual(_visible_error("unexpected parser detail", False), "We have encountered an issue, please contact Jarjish 🥲")
        self.assertEqual(_visible_error(technical, True), technical)


class InvoiceBookingTrackerLatestTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        InvoiceBookingTrackerCheck.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_latest_returns_newest_successful_non_deleted_snapshot(self):
        older_rows = [{"location": "HO", "responsible_person": "Hitanshi", "pending": 2, "records_scanned": 3, "pages_scanned": 1}]
        newest_rows = [{"location": "WADA", "responsible_person": "Vishal", "pending": 5, "records_scanned": 8, "pages_scanned": 2}]
        self.db.add_all([
            InvoiceBookingTrackerCheck(id=1, trigger="scheduled", status="success", total_pending=2, total_records_scanned=3, total_pages_scanned=1, result_json=json.dumps(older_rows), checked_at=datetime(2026, 9, 1, 8), is_deleted=False),
            InvoiceBookingTrackerCheck(id=2, trigger="manual", status="success", total_pending=5, total_records_scanned=8, total_pages_scanned=2, result_json=json.dumps(newest_rows), checked_at=datetime(2026, 9, 2, 9), is_deleted=False),
            InvoiceBookingTrackerCheck(id=3, trigger="manual", status="error", error_message="later failure", checked_at=datetime(2026, 9, 2, 10), is_deleted=False),
            InvoiceBookingTrackerCheck(id=4, trigger="scheduled", status="success", result_json="[]", checked_at=datetime(2026, 9, 3, 8), is_deleted=True),
        ])
        self.db.commit()

        result = latest_tracker(db=self.db)

        self.assertTrue(result["available"])
        self.assertEqual(result["check_id"], 2)
        self.assertEqual(result["rows"], newest_rows)
        self.assertEqual(result["total_pending"], 5)

    def test_latest_has_explicit_empty_state_before_first_success(self):
        result = latest_tracker(db=self.db)
        self.assertFalse(result["available"])
        self.assertEqual(result["rows"], [])

    def test_latest_skips_corrupt_newer_snapshot_and_keeps_last_complete_one(self):
        valid_rows = [{"location": "HO", "responsible_person": "Hitanshi", "pending": 1, "records_scanned": 3, "pages_scanned": 1}]
        self.db.add_all([
            InvoiceBookingTrackerCheck(id=1, trigger="scheduled", status="success", result_json=json.dumps(valid_rows), checked_at=datetime(2026, 9, 1, 8), is_deleted=False),
            InvoiceBookingTrackerCheck(id=2, trigger="manual", status="success", result_json="not-json", checked_at=datetime(2026, 9, 2, 9), is_deleted=False),
            InvoiceBookingTrackerCheck(id=3, trigger="manual", status="success", result_json="[]", checked_at=datetime(2026, 9, 2, 10), is_deleted=False),
            InvoiceBookingTrackerCheck(id=4, trigger="manual", status="success", result_json='[{"location":"HO"}]', checked_at=datetime(2026, 9, 2, 11), is_deleted=False),
        ])
        self.db.commit()

        result = latest_tracker(db=self.db)

        self.assertTrue(result["available"])
        self.assertEqual(result["check_id"], 1)
        self.assertEqual(result["rows"], valid_rows)


if __name__ == "__main__":
    unittest.main()
