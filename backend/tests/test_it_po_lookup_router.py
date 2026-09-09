import unittest

from app.routers.it_po_lookup import _shape_result
from app.services.it_po_lookup.search import SearchResult


class ShapeResultTests(unittest.TestCase):
    """The actual security boundary: IT must never see the ERP document
    number or raw transaction_date, regardless of outcome."""

    def _found_result(self) -> SearchResult:
        return SearchResult(
            po_number="651842",
            outcome="found",
            document_number="20270001460",
            utr_number="HDFCH01251324154",
            transaction_date="2026-09-08T18:21:10",
            value_date="2026-09-08",
            transaction_description="NEFT - ... - 20270001460 - ...",
            transaction_amount=9608.85,
        )

    def test_it_role_never_sees_document_number_or_transaction_date(self):
        shaped = _shape_result(self._found_result(), role="it")
        self.assertNotIn("document_number", shaped)
        self.assertNotIn("transaction_date", shaped)
        self.assertEqual(shaped["utr_number"], "HDFCH01251324154")
        self.assertEqual(shaped["value_date"], "2026-09-08")
        self.assertEqual(shaped["transaction_amount"], 9608.85)

    def test_it_role_sees_nothing_extra_for_a_non_found_outcome(self):
        result = SearchResult(po_number="648811", outcome="payment_in_process", document_number="20270001347")
        shaped = _shape_result(result, role="it")
        self.assertEqual(shaped, {"po_number": "648811", "outcome": "payment_in_process"})

    def test_accounts_role_sees_the_document_number_and_full_detail(self):
        shaped = _shape_result(self._found_result(), role="accounts")
        self.assertEqual(shaped["document_number"], "20270001460")
        self.assertEqual(shaped["transaction_date"], "2026-09-08T18:21:10")

    def test_both_role_sees_the_document_number_and_full_detail(self):
        shaped = _shape_result(self._found_result(), role="both")
        self.assertEqual(shaped["document_number"], "20270001460")


if __name__ == "__main__":
    unittest.main()
