import unittest
from unittest.mock import patch

from app.routers import unaccounted_txn
from app.services import mailer_shared


class MailTemplateDefaultsTests(unittest.TestCase):
    def test_compose_defaults_select_all_reports_and_return_editable_recipients(self):
        recipients = {
            "to": ["accountsincharges@rdc.in", "accountsgroup@rdc.in"],
            "cc": ["manish.modani@rdc.in", "umesh.gawade@rdc.in"],
        }
        with patch.object(
            mailer_shared,
            "get_report_recipient_defaults",
            return_value=recipients,
        ):
            defaults = unaccounted_txn.get_mail_defaults(db=object())

        self.assertEqual(defaults["to"], recipients["to"])
        self.assertEqual(defaults["cc"], recipients["cc"])
        self.assertTrue(defaults["include_ua"])
        self.assertTrue(defaults["include_mrn"])
        self.assertTrue(defaults["include_po"])
        self.assertIn("Unaccounted Transactions", defaults["subject"])
        self.assertIn("1. Unaccounted transactions", defaults["intro"])
        self.assertIn("2. Pending MRN", defaults["intro"])
        self.assertIn("3. Uninvoiced Expenses", defaults["intro"])

    def test_subject_tracks_each_report_selection(self):
        cases = [
            ((True, False, False), "Unaccounted Transactions till Aug-26"),
            ((False, True, False), "Pending MRN till Aug-26"),
            ((False, False, True), "Uninvoiced Expense till Aug-26"),
            (
                (True, True, False),
                "Unaccounted Transactions and Pending MRN till Aug-26",
            ),
            (
                (True, False, True),
                "Unaccounted Transactions and Uninvoiced Expense till Aug-26",
            ),
            (
                (True, True, True),
                "Unaccounted Transactions, Pending MRN and Uninvoiced Expense till Aug-26",
            ),
        ]

        for selections, expected in cases:
            with self.subTest(selections=selections):
                self.assertEqual(
                    mailer_shared.build_default_subject("Aug-26", *selections),
                    expected,
                )

    def test_single_report_intro_is_not_numbered(self):
        intro = mailer_shared.build_default_intro_text(
            include_ua=False,
            include_mrn=True,
            include_po=False,
            month_mrn="Aug-26",
        )

        self.assertIn("Pending MRN till Aug-26", intro)
        self.assertNotIn("1. Pending MRN", intro)
        self.assertNotIn("Unaccounted transactions", intro)

    def test_multiple_report_intro_is_numbered_in_selection_order(self):
        intro = mailer_shared.build_default_intro_text(
            include_ua=True,
            include_mrn=False,
            include_po=True,
            month_ua="Jul-26",
            month_po="Aug-26",
        )

        self.assertIn("1. Unaccounted transactions till Jul-26", intro)
        self.assertIn("2. Uninvoiced Expenses till Aug-26", intro)
        self.assertNotIn("Pending MRN till", intro)
        self.assertIn("Rent and Land Lease have been excluded", intro)


if __name__ == "__main__":
    unittest.main()
