import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import PoLookupBankTransaction
from app.services.it_po_lookup import search
from app.services.it_po_lookup.oracle_lookup import (
    STATUS_BOOKED_PAYMENT_NOT_MADE, STATUS_PAYMENT_ENTRY_NOT_MADE, STATUS_PO_NOT_BOOKED,
)


class ParsePoListTests(unittest.TestCase):
    def test_splits_on_commas_and_newlines_trims_and_dedupes(self):
        raw = "654377, 654423,\n654377\t652440 , ,653660"
        self.assertEqual(search.parse_po_list(raw), ["654377", "654423", "652440", "653660"])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(search.parse_po_list(""), [])
        self.assertEqual(search.parse_po_list("   ,, \n"), [])


class FindMatchingTransactionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        PoLookupBankTransaction.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    _next_id = 1

    def _add(self, description, reference_no, when=datetime(2026, 9, 8, 18, 21, 10)):
        # BigInteger PKs don't get SQLite's autoincrement ROWID alias (only
        # a bare Integer PK does) - assign explicitly, same workaround used
        # by InvoiceBookingTrackerCheck's own tests for the same reason.
        row = PoLookupBankTransaction(
            id=FindMatchingTransactionTests._next_id,
            account_number="00600200000050",
            transaction_date=when,
            transaction_description=description,
            transaction_amount=Decimal("100.00"),
            reference_no=reference_no,
            value_date=date(2026, 9, 8),
        )
        FindMatchingTransactionTests._next_id += 1
        self.db.add(row)
        self.db.commit()
        return row

    def test_matches_document_number_embedded_as_a_distinct_token_in_description(self):
        self._add("NEFT - UTIB0002904 - CX00000000VNGXT9 - 20270001444 - 917020079381137 -  HARRIER", "HDFCH01251322240")
        match = search._find_matching_transaction(self.db, "20270001444")
        self.assertIsNotNone(match)
        self.assertEqual(match.reference_no, "HDFCH01251322240")

    def test_does_not_false_positive_match_a_shorter_number_embedded_inside_a_longer_one(self):
        """"444" must not match inside "20270001444" - this is exactly the
        kind of false positive a naive substring search would produce."""
        self._add("NEFT - UTIB0002904 - CX00000000VNGXT9 - 20270001444 - 917020079381137 -  HARRIER", "HDFCH01251322240")
        match = search._find_matching_transaction(self.db, "444")
        self.assertIsNone(match)

    def test_matches_on_exact_reference_no_even_without_a_description_hit(self):
        self._add("Chq Paid-INWARD TRANS-NOIDA WBO", "000000210561")
        match = search._find_matching_transaction(self.db, "000000210561")
        self.assertIsNotNone(match)

    def test_no_match_returns_none(self):
        self._add("Some unrelated narration", "REF999")
        self.assertIsNone(search._find_matching_transaction(self.db, "20270001444"))

    def test_prefers_the_most_recent_transaction_when_multiple_match(self):
        self._add("... 20270009999 ...", "OLD_REF", when=datetime(2026, 9, 1, 10, 0, 0))
        self._add("... 20270009999 ...", "NEW_REF", when=datetime(2026, 9, 8, 10, 0, 0))
        match = search._find_matching_transaction(self.db, "20270009999")
        self.assertEqual(match.reference_no, "NEW_REF")


class SearchPoNumbersTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        PoLookupBankTransaction.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_four_outcomes_classified_correctly(self):
        self.db.add(
            PoLookupBankTransaction(
                id=1,
                account_number="00600200000050",
                transaction_date=datetime(2026, 9, 8, 18, 21, 10),
                transaction_description="NEFT - ... - 20270001460 - ... -  SAI COMPUTER CARE",
                transaction_amount=Decimal("9608.85"),
                reference_no="HDFCH01251324154",
                value_date=date(2026, 9, 8),
            )
        )
        self.db.commit()

        fake_statuses = {
            "651842": "20270001460",  # has a document number AND a stored matching transaction -> found
            "648811": "20270001347",  # has a document number but no stored transaction -> in process
            "654377": STATUS_PO_NOT_BOOKED,
            "654423": STATUS_PAYMENT_ENTRY_NOT_MADE,
            "654634": STATUS_BOOKED_PAYMENT_NOT_MADE,
            # "999999999" deliberately absent -> po_not_found
        }
        with patch.object(search, "fetch_document_numbers", return_value=fake_statuses):
            results = search.search_po_numbers(self.db, oracle_cfg=object(), raw_po_text="651842,648811,654377,654423,654634,999999999")

        by_po = {r.po_number: r for r in results}
        self.assertEqual(by_po["651842"].outcome, "found")
        self.assertEqual(by_po["651842"].utr_number, "HDFCH01251324154")
        self.assertEqual(by_po["651842"].transaction_amount, 9608.85)
        self.assertEqual(by_po["648811"].outcome, "payment_in_process")
        self.assertEqual(by_po["648811"].document_number, "20270001347")
        self.assertEqual(by_po["654377"].outcome, "payment_not_processed")
        self.assertEqual(by_po["654423"].outcome, "payment_not_processed")
        self.assertEqual(by_po["654634"].outcome, "payment_not_processed")
        self.assertEqual(by_po["999999999"].outcome, "po_not_found")

    def test_empty_po_text_returns_no_results_without_calling_oracle(self):
        with patch.object(search, "fetch_document_numbers") as mocked:
            results = search.search_po_numbers(self.db, oracle_cfg=object(), raw_po_text="   ")
        self.assertEqual(results, [])
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
