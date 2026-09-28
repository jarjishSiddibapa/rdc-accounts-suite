"""Coverage for the Untagged Invoices Report Generator's processing
pipeline: header/row cleanup, Location mapping, ageing-bucket recompute
(from an as-on date, not the source file's own frozen buckets), the two
Ageing/Summary pairs (Zero to 1k, Untagged - both restricted to
Type = Transactions, with the Type column dropped from their output),
and the 5-sheet workbook's shape."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import openpyxl
import pandas as pd

from app.services.untagged_invoices.processor import (
    BUCKET_COLS,
    LOCATION_COL,
    _bucket_index,
    add_location_column,
    build_untagged_ageing,
    build_untagged_summary,
    build_zero_to_1k_ageing,
    build_zero_to_1k_summary,
    missing_incharge_mappings,
    missing_location_mappings,
    read_ageing_file,
    recompute_ageing_buckets,
    write_report,
)

_TYPE_COL = "Type (Transaction/Receipt)"

_HEADERS = [
    "Customer Name", "Customer Account", "Customer Group Name", "Location Number",
    "Location Name", "Sales Person Name", "Credit Limit", "Payment Terms",
    "Invoice No/ Receipt No", "Invoice/ Receipt Date", "Type (Transaction/Receipt)",
    "GL Date", "Invoice Type", "Accounted Invoice", "Accounted Outstanding",
    "Sum of On Account/Prepayment Amount", "Sum of Unapplied Amount", "Days O/S",
    *BUCKET_COLS, "Receivable Account",
]


class _LogQueue:
    def __init__(self):
        self.messages = []

    def put(self, item):
        self.messages.append(item)


def _make_ageing_workbook(path, data_rows, include_grand_total=True):
    """Build a minimal Oracle-shaped Aging export: 13 metadata rows, the
    real header at row 14, then data rows, an optional Grand Total row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Aging"
    for r in range(1, 14):
        ws.cell(row=r, column=1, value=f"meta {r}")
    for c, h in enumerate(_HEADERS, 1):
        ws.cell(row=14, column=c, value=h)
    row_num = 15
    for row in data_rows:
        for c, h in enumerate(_HEADERS, 1):
            ws.cell(row=row_num, column=c, value=row.get(h))
        row_num += 1
    ws.cell(row=row_num, column=1, value=None)  # blank separator row
    row_num += 1
    if include_grand_total:
        ws.cell(row=row_num, column=1, value="Grand Total:")
        ws.cell(row=row_num, column=15, value=999999.99)
    wb.save(path)


def _base_row(**overrides):
    row = {
        "Customer Name": "Acme Co",
        "Customer Account": 1001,
        "Location Name": "Coimbatore - Saravanampatti",
        "Invoice/ Receipt Date": dt.datetime(2026, 1, 1),
        "Type (Transaction/Receipt)": "Transactions",
        "Accounted Outstanding": 500.0,
    }
    row.update(overrides)
    return row


class BucketIndexTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(_bucket_index(0), 0)
        self.assertEqual(_bucket_index(30), 0)
        self.assertEqual(_bucket_index(31), 1)
        self.assertEqual(_bucket_index(60), 1)
        self.assertEqual(_bucket_index(61), 2)
        self.assertEqual(_bucket_index(360), 6)
        self.assertEqual(_bucket_index(361), 7)
        self.assertEqual(_bucket_index(10_000), 7)


class ReadAgeingFileTests(unittest.TestCase):
    def test_drops_grand_total_and_blank_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ageing.xlsx"
            _make_ageing_workbook(path, [_base_row(), _base_row(**{"Customer Name": "Beta Ltd"})])
            df = read_ageing_file(str(path), _LogQueue())
            self.assertEqual(len(df), 2)
            self.assertNotIn("Grand Total:", df["Customer Name"].tolist())

    def test_empty_file_raises(self):
        from app.jobs import JobUserError
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ageing.xlsx"
            _make_ageing_workbook(path, [])
            with self.assertRaises(JobUserError):
                read_ageing_file(str(path), _LogQueue())


class LocationMappingTests(unittest.TestCase):
    def test_add_location_column_after_location_name(self):
        df = pd.DataFrame([_base_row(), _base_row(**{"Location Name": "unmapped place"})])
        location_map = {"COIMBATORE - SARAVANAMPATTI": "COIMBATORE+TRICHY"}
        out = add_location_column(df, location_map)
        cols = list(out.columns)
        self.assertEqual(cols.index(LOCATION_COL), cols.index("Location Name") + 1)
        self.assertEqual(out[LOCATION_COL].tolist(), ["COIMBATORE+TRICHY", ""])

    def test_missing_location_and_incharge_mappings(self):
        df = pd.DataFrame([_base_row(), _base_row(**{"Location Name": "unmapped place"})])
        location_map = {"COIMBATORE - SARAVANAMPATTI": "COIMBATORE+TRICHY"}
        missing = missing_location_mappings(df, location_map)
        self.assertEqual(missing, ["UNMAPPED PLACE"])

        out = add_location_column(df, location_map)
        missing_incharge = missing_incharge_mappings(out[LOCATION_COL].tolist(), {})
        self.assertIn("COIMBATORE+TRICHY", missing_incharge)
        # blank/unmapped Location never shows up as an "incharge" gap of its own
        self.assertNotIn("", missing_incharge)


class RecomputeAgeingBucketsTests(unittest.TestCase):
    def test_places_amount_in_the_correct_single_bucket(self):
        df = pd.DataFrame([_base_row(
            **{"Invoice/ Receipt Date": dt.datetime(2026, 1, 1), "Accounted Outstanding": 16812.05},
        )])
        as_on = dt.date(2026, 1, 1) + dt.timedelta(days=655)  # -> bucket "361-999999 Days "
        out = recompute_ageing_buckets(df, as_on, _LogQueue())
        self.assertEqual(out["Days O/S"].iloc[0], 655)
        for col in BUCKET_COLS[:-1]:
            self.assertEqual(out[col].iloc[0], 0.0)
        self.assertEqual(out["361-999999 Days "].iloc[0], 16812.05)

    def test_unparseable_date_leaves_buckets_blank(self):
        df = pd.DataFrame([_base_row(**{"Invoice/ Receipt Date": "not a date"})])
        out = recompute_ageing_buckets(df, dt.date(2026, 1, 1), _LogQueue())
        self.assertTrue(pd.isna(out["Days O/S"].iloc[0]))
        self.assertTrue(pd.isna(out["0-30 Days"].iloc[0]))


class PivotTests(unittest.TestCase):
    def _prepared(self, rows, as_on):
        df = pd.DataFrame(rows)
        df = add_location_column(df, {"LOC A": "Location A", "LOC B": "Location B"})
        return recompute_ageing_buckets(df, as_on, _LogQueue())

    def test_zero_to_1k_ageing_filters_by_type_and_amount_and_groups_by_location(self):
        as_on = dt.date(2026, 1, 31)  # 30 days after invoice date -> bucket 0-30
        rows = [
            _base_row(**{"Location Name": "LOC A", "Accounted Outstanding": 500.0}),
            _base_row(**{"Location Name": "LOC A", "Accounted Outstanding": 250.0}),
            _base_row(**{"Location Name": "LOC A", "Accounted Outstanding": 5000.0}),  # excluded: >= 1000
            _base_row(**{"Location Name": "LOC A", _TYPE_COL: "Receipts",
                         "Accounted Outstanding": 300.0}),  # excluded: not a Transaction
            _base_row(**{"Location Name": "LOC B", "Accounted Outstanding": -10.0}),   # excluded: not > 0
        ]
        df = self._prepared(rows, as_on)
        ageing = build_zero_to_1k_ageing(df)
        self.assertEqual(len(ageing), 2)  # the >=1000, Receipts, and negative rows are excluded
        self.assertNotIn(_TYPE_COL, ageing.columns)
        pivot = build_zero_to_1k_summary(ageing, {"Location A": "Alice"})

        self.assertEqual(list(pivot[LOCATION_COL]), ["Location A"])
        self.assertAlmostEqual(pivot["Total O/S"].iloc[0], 750.0)
        self.assertEqual(pivot["Account Incharges"].iloc[0], "Alice")
        self.assertAlmostEqual(
            pivot["Above 30 days"].iloc[0],
            pivot["Total O/S"].iloc[0] - pivot["0-30 Days"].iloc[0] - pivot["31-60 Days"].iloc[0],
        )

    def test_untagged_ageing_and_summary(self):
        as_on = dt.date(2026, 1, 31)
        rows = [
            _base_row(**{"Location Name": "LOC A", _TYPE_COL: "Transactions",
                         "Accounted Outstanding": -1000.0}),
            _base_row(**{"Location Name": "LOC A", _TYPE_COL: "Receipts",
                         "Accounted Outstanding": -500.0}),   # excluded: not a Transaction
            _base_row(**{"Location Name": "LOC A", _TYPE_COL: "Transactions",
                         "Accounted Outstanding": 50.0}),      # excluded: outstanding not < 0
        ]
        df = self._prepared(rows, as_on)
        ageing = build_untagged_ageing(df)
        self.assertEqual(len(ageing), 1)
        self.assertEqual(ageing["Accounted Outstanding"].iloc[0], -1000.0)
        self.assertNotIn(_TYPE_COL, ageing.columns)

        summary = build_untagged_summary(ageing, {"Location A": "Alice"})
        self.assertEqual(list(summary[LOCATION_COL]), ["Location A"])
        self.assertAlmostEqual(summary["Accounted Outstanding"].iloc[0], -1000.0)
        self.assertAlmostEqual(
            summary["Above 30 Days"].iloc[0],
            summary["Accounted Outstanding"].iloc[0] - summary["0-30 Days"].iloc[0],
        )


class WriteReportTests(unittest.TestCase):
    def test_sheet_order_and_grand_total_formula(self):
        as_on = dt.date(2026, 1, 31)
        rows = [
            _base_row(**{"Location Name": "LOC A", "Accounted Outstanding": -1000.0}),  # untagged
            _base_row(**{"Location Name": "LOC A", "Accounted Outstanding": 500.0}),    # zero to 1k
            _base_row(**{"Location Name": "LOC A", _TYPE_COL: "Receipts",
                         "Accounted Outstanding": -2000.0}),  # Receipts: in Ageing only
        ]
        df = pd.DataFrame(rows)
        df = add_location_column(df, {"LOC A": "Location A"})
        df = recompute_ageing_buckets(df, as_on, _LogQueue())
        zero_to_1k_ageing = build_zero_to_1k_ageing(df)
        zero_to_1k_summary = build_zero_to_1k_summary(zero_to_1k_ageing, {"Location A": "Alice"})
        untagged_ageing = build_untagged_ageing(df)
        untagged_summary = build_untagged_summary(untagged_ageing, {"Location A": "Alice"})

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "out.xlsx"
            write_report(df, untagged_summary, untagged_ageing, zero_to_1k_summary, zero_to_1k_ageing,
                         out_path, as_on, log_q=_LogQueue())

            wb = openpyxl.load_workbook(out_path)
            self.assertEqual(
                wb.sheetnames,
                ["Untagged Summary", "Untagged Ageing", "Zero to 1k Summary", "Zero to 1k Ageing",
                 "Ageing"],
            )

            zero_to_1k_summary_ws = wb["Zero to 1k Summary"]
            grand_total_row = zero_to_1k_summary_ws.max_row
            self.assertEqual(zero_to_1k_summary_ws.cell(row=grand_total_row, column=1).value, "Grand Total")
            total_cell = zero_to_1k_summary_ws.cell(row=grand_total_row, column=3).value
            self.assertTrue(str(total_cell).startswith("=SUBTOTAL(9,"))

            summary_ws = wb["Untagged Summary"]
            summary_grand_row = summary_ws.max_row
            summary_total_cell = summary_ws.cell(row=summary_grand_row, column=3).value
            self.assertTrue(str(summary_total_cell).startswith("=SUBTOTAL(9,"))

            zero_to_1k_ws = wb["Zero to 1k Ageing"]
            self.assertEqual(zero_to_1k_ws.max_row - 1, len(zero_to_1k_ageing))

            # Type column dropped from both Ageing sub-sheets, but kept on the
            # raw "Ageing" sheet, which also keeps the Receipts row.
            untagged_header = [c.value for c in wb["Untagged Ageing"][1]]
            self.assertNotIn(_TYPE_COL, untagged_header)
            zero_to_1k_header = [c.value for c in wb["Zero to 1k Ageing"][1]]
            self.assertNotIn(_TYPE_COL, zero_to_1k_header)

            ageing_ws = wb["Ageing"]
            ageing_header = [c.value for c in ageing_ws[1]]
            self.assertIn(_TYPE_COL, ageing_header)
            self.assertEqual(ageing_ws.max_row - 1, len(df))


if __name__ == "__main__":
    unittest.main()
