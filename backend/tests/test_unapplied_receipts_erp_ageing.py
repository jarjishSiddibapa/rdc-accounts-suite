"""The Unapplied Receipts report takes its ageing from the ERP: no ageing file is uploaded."""

import unittest

import pandas as pd

from app.services.erp_ageing import erp_frame
from app.services.unapplied_receipts import processor


class _Log:
    def __init__(self):
        self.messages = []

    def put(self, item):
        self.messages.append(item)


def _ageing_from_erp():
    columns = ["CUSTOMER_NAME", "CUSTOMER_ACCOUNT", "LOCATION", "SALESPERSON", "CREDITLIMIT", "PAYMENT_TERM",
               "TRX_NUMBER", "DUE_DATE", "INVOICE_RCPT_CLASS", "OUTSTANDING_AMOUNT", "OVER_DUE_DAYS"]
    rows = [
        ("Has Invoices", "1001", "Wada", "Asha", None, 30, "INV1", "2026-09-01", "Invoice", 500, 20),
        ("Has Invoices", "1001", "Wada", "Asha", None, 30, "RCP1", "2026-09-05", "Receipts", -100, 16),
        ("Only Receipts", "2002", "Wada", "Ravi", None, None, "RCP2", "2026-09-05", "Receipts", -900, 16),
    ]
    return erp_frame(columns, rows, include_buckets=False)


class ClassifyWithErpAgeingTests(unittest.TestCase):
    def test_a_customer_with_only_receipts_in_the_erp_ageing_is_an_advance_customer(self):
        register = pd.DataFrame({
            "Customer Number": ["1001", "2002", "3003"],
            "Customer Name": ["Has Invoices", "Only Receipts", "Unknown"],
        })
        log = _Log()
        main, advance, salespeople = processor.classify_advance_customers(
            register, None, log, ageing_frame=_ageing_from_erp(),
        )
        self.assertEqual(list(main["Customer Number"]), ["1001"])
        self.assertEqual(sorted(advance["Customer Number"]), ["2002", "3003"])   # not in the ageing -> advance
        self.assertEqual(salespeople["1001"], "Asha")
        self.assertTrue(any("ERP" in text for _, text in log.messages))


if __name__ == "__main__":
    unittest.main()
