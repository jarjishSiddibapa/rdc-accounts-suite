import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from app.services.iocl_balance import monitor


class IoclBalanceParsingTests(unittest.TestCase):
    def test_exact_ccms_balance_wins_over_rounded_wallet_balance(self):
        text = "Wallet Balance ₹ 5.2 L\nCCMS Balance Rs. 4,87,654.32"
        self.assertEqual(monitor.extract_balance_from_text(text), Decimal("487654.32"))

    def test_indian_suffixes_are_parsed(self):
        self.assertEqual(monitor.parse_amount("12.47", "L"), Decimal("1247000.00"))
        self.assertEqual(monitor.parse_amount("1.5", "Cr"), Decimal("15000000.00"))
        self.assertEqual(monitor.parse_amount("50", "K"), Decimal("50000.00"))

    def test_reference_style_ccms_text_is_detected(self):
        self.assertEqual(
            monitor.extract_balance_from_text("Online CCMS Recharge\nCCMS Balance : ₹ 5,00,000.00"),
            Decimal("500000.00"),
        )


class IoclThresholdTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 29, 12, 0)

    def due(self, **overrides):
        values = dict(
            previous=Decimal("600000"),
            current=Decimal("490000"),
            threshold=Decimal("500000"),
            last_notification_at=None,
            repeat_hours=30,
            now=self.now,
        )
        values.update(overrides)
        return monitor.threshold_reminder_due(**values)

    def test_entering_below_threshold_alerts_immediately(self):
        self.assertTrue(self.due())

    def test_remaining_below_threshold_waits_for_repeat_interval(self):
        self.assertFalse(self.due(
            previous=Decimal("480000"),
            last_notification_at=self.now - timedelta(hours=29, minutes=59),
        ))
        self.assertTrue(self.due(
            previous=Decimal("480000"),
            last_notification_at=self.now - timedelta(hours=30),
        ))

    def test_recovery_stops_reminders(self):
        self.assertFalse(self.due(current=Decimal("500000")))
        self.assertFalse(self.due(current=Decimal("550000")))

    def test_a_new_drop_after_recovery_alerts_even_if_previous_mail_is_recent(self):
        self.assertTrue(self.due(
            previous=Decimal("550000"),
            last_notification_at=self.now - timedelta(hours=1),
        ))

    def test_human_threshold_labels(self):
        cases = {
            Decimal("500000"): "5 lakh",
            Decimal("450000"): "4.5 lakh",
            Decimal("100000"): "1 lakh",
            Decimal("50000"): "50 thousand",
            Decimal("0"): "0",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(monitor.format_threshold(value), expected)


class IoclTemplateTests(unittest.TestCase):
    def test_ordinal_business_date(self):
        cases = {
            date(2026, 8, 1): "1st August 2026",
            date(2026, 8, 2): "2nd August 2026",
            date(2026, 8, 3): "3rd August 2026",
            date(2026, 8, 11): "11th August 2026",
            date(2026, 8, 27): "27th August 2026",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(monitor.format_business_date(value), expected)

    def test_templates_reject_unknown_placeholders(self):
        monitor.validate_template("Balance {balance} on {date}", monitor.DAILY_TEMPLATE_FIELDS)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            monitor.validate_template("Balance {password}", monitor.DAILY_TEMPLATE_FIELDS)

    def test_default_mail_text_has_no_duplicate_rupee_prefix(self):
        rendered = monitor._render(monitor.DEFAULT_DAILY_BODY, Decimal("500000"))
        self.assertIn("is Rs. 5,00,000.00.", rendered)
        self.assertNotIn("Rs. Rs.", rendered)

    def test_balance_is_bold_in_daily_and_alert_email_bodies(self):
        _, daily_body = monitor.render_preview(
            monitor.DEFAULT_DAILY_SUBJECT,
            monitor.DEFAULT_DAILY_BODY,
            Decimal("500000"),
        )
        _, alert_body = monitor.render_preview(
            monitor.DEFAULT_ALERT_SUBJECT,
            monitor.DEFAULT_ALERT_BODY,
            Decimal("450000"),
            Decimal("500000"),
        )
        self.assertIn("<strong>Rs. 5,00,000.00</strong>", daily_body)
        self.assertIn("<strong>Rs. 4,50,000.00</strong>", alert_body)


class IoclRetryTests(unittest.TestCase):
    def test_succeeds_on_third_attempt_and_discards_saved_session_after_first(self):
        snapshot = {
            "login_url": "https://example.test/login",
            "username": "user",
            "password": "secret",
            "saved_session": {"cookies": [{"name": "old"}]},
            "login_timeout_seconds": 60,
        }
        with patch.object(
            monitor,
            "fetch_balance",
            side_effect=[
                RuntimeError("stale session"),
                RuntimeError("portal still loading"),
                (Decimal("450000"), {"cookies": [{"name": "fresh"}]}),
            ],
        ) as mocked:
            balance, session, attempts = monitor.fetch_balance_with_retries(snapshot)

        self.assertEqual(balance, Decimal("450000"))
        self.assertEqual(session["cookies"][0]["name"], "fresh")
        self.assertEqual(attempts, 3)
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(mocked.call_args_list[0].kwargs["saved_session"], snapshot["saved_session"])
        self.assertIsNone(mocked.call_args_list[1].kwargs["saved_session"])
        self.assertIsNone(mocked.call_args_list[2].kwargs["saved_session"])

    def test_fails_only_after_three_attempts(self):
        snapshot = {
            "login_url": "https://example.test/login",
            "username": "user",
            "password": "secret",
            "saved_session": None,
            "login_timeout_seconds": 60,
        }
        with patch.object(
            monitor,
            "fetch_balance",
            side_effect=RuntimeError("Financials unavailable"),
        ) as mocked:
            with self.assertRaisesRegex(RuntimeError, "failed after 3 attempts"):
                monitor.fetch_balance_with_retries(snapshot)
        self.assertEqual(mocked.call_count, 3)


class _FakeLoginField:
    def __init__(self, *, editable: bool):
        self.editable = editable
        self.clicks = 0
        self.filled = None

    def is_editable(self):
        return self.editable

    def click(self, timeout=None):
        self.clicks += 1
        self.editable = True

    def fill(self, value, timeout=None):
        if not self.editable:
            raise RuntimeError("readonly")
        self.filled = value


class LoginFieldInteractionTests(unittest.TestCase):
    def test_readonly_portal_field_is_clicked_before_fill(self):
        field = _FakeLoginField(editable=False)

        monitor._fill_login_field(field, "ULTRAFINE", "User ID")

        self.assertEqual(field.clicks, 1)
        self.assertEqual(field.filled, "ULTRAFINE")

    def test_already_editable_field_is_filled_without_extra_click(self):
        field = _FakeLoginField(editable=True)

        monitor._fill_login_field(field, "secret", "Password")

        self.assertEqual(field.clicks, 0)
        self.assertEqual(field.filled, "secret")


class _FakeNavElement:
    def __init__(self, visible: bool):
        self._visible = visible
        self.clicked = False

    def is_visible(self) -> bool:
        return self._visible

    def click(self, timeout=None):
        self.clicked = True


class _FakeNavLocator:
    def __init__(self, elements):
        self._elements = elements

    def count(self):
        return len(self._elements)

    def nth(self, index):
        return self._elements[index]


class _FakeOverlayLocator:
    @property
    def first(self):
        return self

    def wait_for(self, state=None, timeout=None):
        pass


class _FakePage:
    def __init__(self, elements):
        self._locator = _FakeNavLocator(elements)

    def get_by_text(self, label, exact=False):
        return self._locator

    def locator(self, selector):
        return _FakeOverlayLocator()

    def wait_for_timeout(self, ms):
        pass


class ClickNavVisibilityTests(unittest.TestCase):
    """Regression coverage for a production failure: the IOCL portal's SPA
    keeps more than one element matching a nav label in the DOM at once
    (e.g. a hidden duplicate), so a plain get_by_text(...).first can latch
    onto the hidden one and time out waiting for it to become visible even
    though a visible match exists elsewhere on the page."""

    def test_skips_a_hidden_duplicate_and_finds_the_visible_match(self):
        hidden = _FakeNavElement(visible=False)
        visible = _FakeNavElement(visible=True)
        page = _FakePage([hidden, visible])

        found = monitor._find_visible_nav_link(page, "Financials", timeout_ms=200)

        self.assertIs(found, visible)

    def test_returns_none_when_every_match_stays_hidden(self):
        page = _FakePage([_FakeNavElement(visible=False), _FakeNavElement(visible=False)])

        found = monitor._find_visible_nav_link(page, "Financials", timeout_ms=100)

        self.assertIsNone(found)

    def test_click_nav_raises_a_clear_error_when_nothing_is_visible(self):
        page = _FakePage([_FakeNavElement(visible=False)])

        with self.assertRaisesRegex(RuntimeError, "No visible 'Financials'"):
            monitor._click_nav(page, "Financials", timeout_ms=100)


class _FakeLoginRoutePage:
    def __init__(self, urls):
        self._urls = urls
        self._index = 0

    @property
    def url(self):
        return self._urls[min(self._index, len(self._urls) - 1)]

    def wait_for_timeout(self, ms):
        self._index += 1


class LoginCompletionTests(unittest.TestCase):
    def test_transient_redirect_from_expired_session_is_not_login_success(self):
        page = _FakeLoginRoutePage(
            [
                "https://beta.iocxtrapower.com/account/login?returnUrl=%2F",
                "https://beta.iocxtrapower.com/Quicklinks",
                "https://beta.iocxtrapower.com/account/login?returnUrl=%2F",
            ]
        )

        self.assertFalse(monitor._wait_until_logged_in(page, timeout_seconds=2))

    def test_stable_authenticated_route_is_login_success(self):
        page = _FakeLoginRoutePage(
            [
                "https://beta.iocxtrapower.com/account/login?returnUrl=%2F",
                "https://beta.iocxtrapower.com/Quicklinks",
                "https://beta.iocxtrapower.com/Quicklinks",
                "https://beta.iocxtrapower.com/Quicklinks",
            ]
        )

        self.assertTrue(monitor._wait_until_logged_in(page, timeout_seconds=2))


if __name__ == "__main__":
    unittest.main()
