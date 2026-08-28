import unittest
from datetime import date
from decimal import Decimal

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
    def test_first_observation_emits_only_nearest_reached_threshold(self):
        self.assertEqual(
            monitor.crossed_thresholds(None, Decimal("380000"), Decimal("500000"), Decimal("50000")),
            [Decimal("400000")],
        )

    def test_one_drop_can_cross_multiple_thresholds(self):
        self.assertEqual(
            monitor.crossed_thresholds(
                Decimal("510000"), Decimal("440000"), Decimal("500000"), Decimal("50000")
            ),
            [Decimal("500000"), Decimal("450000")],
        )

    def test_rising_or_unchanged_balance_does_not_alert(self):
        self.assertEqual(
            monitor.crossed_thresholds(
                Decimal("450000"), Decimal("475000"), Decimal("500000"), Decimal("50000")
            ),
            [],
        )

    def test_zero_is_an_explicit_threshold(self):
        self.assertEqual(
            monitor.crossed_thresholds(
                Decimal("25000"), Decimal("0"), Decimal("500000"), Decimal("50000")
            ),
            [Decimal("0")],
        )

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
