import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.jobs import JobUserError
from app.routers.ultrafine_fse_reminder import download_template
from app.services.ultrafine_fse_reminder import mapping_store, processor
from app.services.ultrafine_fse_reminder.models import FseEmailMap


def _write_tracker(path: Path, include_decoy: bool = True, include_junk: bool = True) -> None:
    """Builds a small workbook shaped like the real 'Coll vs Target' sheet:
    a title cell, a header row with a stale #REF! decoy Target column (no
    matching Received column right after it) before the real Target/
    Received/Short Fall triplet, two FSEs each with two party rows and their
    own subtotal row, a Grand Total row, then unrelated junk below it."""
    wb = Workbook()
    ws = wb.active
    ws.title = processor.SHEET_NAME
    ws.append([None, None, "Collection VS Target Summary for Sep-26"])
    header = ["FSE", "FSE", "Party's Name"]
    if include_decoy:
        header += ["Collection Target considering dues upto  31-Jul-26"]
    header += [
        "Collection Target considering dues upto  30-Sep-26",
        "Coll Received as on 16-Sep-26",
        "Short Fall",
    ]
    ws.append(header)

    def row(fse, party, target, received, shortfall):
        values = [fse, fse, party]
        if include_decoy:
            values.append("#REF!")
        values += [target, received, shortfall]
        ws.append(values)

    row("Abhishek Nayak", "Customer A - Drs", 3.04, 2.44, 0.60)
    row("Abhishek Nayak", "Customer B - Drs", 10.73, 0, 10.73)
    row("Abhishek Nayak", None, None, None, None)
    ws.cell(ws.max_row, 3, "Abhishek Nayak Total")
    ws.cell(ws.max_row, 1, "Abhishek Nayak")
    ws.cell(ws.max_row, (5 if include_decoy else 4), 13.77)
    ws.cell(ws.max_row, (6 if include_decoy else 5), 2.44)
    ws.cell(ws.max_row, (7 if include_decoy else 6), 11.33)

    row("Balram Chakrawarti", "Customer C - Drs", 19.42, 0, 19.42)
    ws.append([None, None, "Balram Chakrawarti Total", *(["#REF!"] if include_decoy else []), 19.42, 0, 19.42])
    ws.cell(ws.max_row, 1, "Balram Chakrawarti")

    ws.append([None, None, "Grand Total", *(["#REF!"] if include_decoy else []), 33.19, 2.44, 30.75])

    if include_junk:
        ws.append([None, None, "Collection Received which is not List in Ageing"])
        ws.append([None, "Customer Name", "Customer Name", "s" if include_decoy else None, ".", "Amount"])
        ws.append([None, "Bandu Naik", "Some Other Customer - Drs"])

    wb.save(path)


class TemplateGenerationTests(unittest.TestCase):
    def test_generated_template_has_single_fse_column_and_parses_cleanly(self):
        import openpyxl

        response = download_template()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.xlsx"
            path.write_bytes(response.body)

            wb = openpyxl.load_workbook(path)
            ws = wb[processor.SHEET_NAME]
            header_row = processor.find_header_row(ws)
            headers = [ws.cell(header_row, c).value for c in range(1, ws.max_column + 1)]
            self.assertEqual(headers.count("FSE"), 1, f"expected exactly one 'FSE' column, got headers={headers}")

            parsed = processor.read_coll_vs_target(str(path))
            groups = processor.group_by_fse(parsed["rows"])
            self.assertIn("Abhishek Nayak", groups)
            self.assertIn("Balram Chakrawarti", groups)
            self.assertAlmostEqual(groups["Abhishek Nayak"]["total_target"], 13.7682355, places=4)
            self.assertAlmostEqual(groups["Balram Chakrawarti"]["total_target"], 19.4248, places=4)


class ColumnDetectionTests(unittest.TestCase):
    def test_skips_decoy_target_column_and_finds_real_triplet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            _write_tracker(path, include_decoy=True)
            parsed = processor.read_coll_vs_target(str(path))
            self.assertEqual(parsed["target_header"], "Collection Target considering dues upto  30-Sep-26")
            self.assertEqual(parsed["received_header"], "Coll Received as on 16-Sep-26")
            self.assertEqual(parsed["as_on_raw"], "16-Sep-26")
            self.assertEqual(parsed["as_on_long"], "16-Sep-2026")

    def test_works_without_decoy_column_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            _write_tracker(path, include_decoy=False)
            parsed = processor.read_coll_vs_target(str(path))
            self.assertEqual(parsed["target_header"], "Collection Target considering dues upto  30-Sep-26")

    def test_missing_fse_header_raises_job_user_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = processor.SHEET_NAME
            ws.append(["Not FSE", "Something"])
            wb.save(path)
            with self.assertRaises(JobUserError):
                processor.read_coll_vs_target(str(path))

    def test_missing_sheet_raises_job_user_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            Workbook().save(path)
            with self.assertRaises(JobUserError):
                processor.read_coll_vs_target(str(path))


class RowParsingTests(unittest.TestCase):
    def test_skips_subtotal_rows_stops_at_grand_total_ignores_junk_below(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            _write_tracker(path)
            parsed = processor.read_coll_vs_target(str(path))
            parties = [row["party"] for row in parsed["rows"]]
            self.assertEqual(parties, ["Customer A - Drs", "Customer B - Drs", "Customer C - Drs"])
            self.assertNotIn("Bandu Naik", [row["fse"] for row in parsed["rows"]])

    def test_group_totals_match_source_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            _write_tracker(path)
            parsed = processor.read_coll_vs_target(str(path))
            groups = processor.group_by_fse(parsed["rows"])
            self.assertAlmostEqual(groups["Abhishek Nayak"]["total_target"], 13.77)
            self.assertAlmostEqual(groups["Abhishek Nayak"]["total_received"], 2.44)
            self.assertAlmostEqual(groups["Balram Chakrawarti"]["total_target"], 19.42)


class RecipientDedupTests(unittest.TestCase):
    def test_individual_cc_drops_fse_who_is_also_a_standing_cc(self):
        to = ["Harish Ludhani <harish.ludhani@ultrafine.in>"]
        cc = processor.dedupe_cc_against_to(to, processor.INDIVIDUAL_CC)
        self.assertNotIn("Harish Ludhani <harish.ludhani@ultrafine.in>", cc)
        self.assertIn("Devanand Singh <devanand.singh@ultrafine.in>", cc)

    def test_broadcast_cc_drops_salesperson_who_is_also_a_fixed_cc(self):
        to = ["Harish Ludhani <harish.ludhani@ultrafine.in>", "Abhishek Nayak <abhishek.nayak@ultrafine.in>"]
        cc = processor.dedupe_cc_against_to(to, processor.BROADCAST_CC)
        self.assertEqual(len(cc), len(processor.BROADCAST_CC))  # none of BROADCAST_CC overlaps this `to`

    def test_extract_address_handles_bare_and_display_name_forms(self):
        self.assertEqual(processor.extract_address("a@b.com"), "a@b.com")
        self.assertEqual(processor.extract_address("Name <A@B.COM>"), "a@b.com")


class BuildSendPlanTests(unittest.TestCase):
    def test_missing_email_flagged_present_email_wired_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            _write_tracker(path)
            parsed = processor.read_coll_vs_target(str(path))
            mapping = {"Abhishek Nayak": "abhishek.nayak@ultrafine.in"}
            plan = processor.build_send_plan(parsed, mapping, signature="")

            by_name = {row["fse_name"]: row for row in plan["individual"]}
            self.assertFalse(by_name["Abhishek Nayak"]["missing_email"])
            self.assertEqual(by_name["Abhishek Nayak"]["to"], ["Abhishek Nayak <abhishek.nayak@ultrafine.in>"])
            self.assertTrue(by_name["Balram Chakrawarti"]["missing_email"])
            self.assertEqual(by_name["Balram Chakrawarti"]["to"], [])

            self.assertEqual(plan["broadcast"]["to"], ["Abhishek Nayak <abhishek.nayak@ultrafine.in>"])
            self.assertAlmostEqual(plan["broadcast"]["total_target"], 13.77 + 19.42)

    def test_subject_uses_as_on_date_from_sheet_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            _write_tracker(path)
            parsed = processor.read_coll_vs_target(str(path))
            plan = processor.build_send_plan(parsed, {}, signature="")
            self.assertEqual(
                plan["individual"][0]["subject"],
                "Collection Target VS Actual Collection Received as on 16-Sep-26",
            )
            self.assertEqual(plan["broadcast"]["subject"], plan["individual"][0]["subject"])


class MappingStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine, tables=[FseEmailMap.__table__])
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_upsert_load_delete_round_trip(self):
        db = self.Session()
        try:
            mapping_store.upsert_fse_mapping(db, "Abhishek Nayak", "abhishek.nayak@ultrafine.in")
            self.assertEqual(mapping_store.load_all(db), {"Abhishek Nayak": "abhishek.nayak@ultrafine.in"})

            mapping_store.upsert_fse_mapping(db, "Abhishek Nayak", "new@ultrafine.in")
            self.assertEqual(mapping_store.load_all(db), {"Abhishek Nayak": "new@ultrafine.in"})

            self.assertTrue(mapping_store.delete_fse_mapping(db, "Abhishek Nayak"))
            self.assertEqual(mapping_store.load_all(db), {})
            self.assertFalse(mapping_store.delete_fse_mapping(db, "Abhishek Nayak"))
        finally:
            db.close()

    def test_two_concurrent_upserts_for_different_fses_do_not_clobber_each_other(self):
        db_a = self.Session()
        db_b = self.Session()
        try:
            mapping_store.upsert_fse_mapping(db_a, "FSE A", "a@x.com")
            mapping_store.upsert_fse_mapping(db_b, "FSE B", "b@x.com")
            self.assertEqual(mapping_store.load_all(db_a), {"FSE A": "a@x.com", "FSE B": "b@x.com"})
        finally:
            db_a.close()
            db_b.close()


if __name__ == "__main__":
    unittest.main()
