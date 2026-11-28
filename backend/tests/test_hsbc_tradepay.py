"""RDC HSBC Tradepay CSV Generation: the file's layout, the rules of its values, and what the routes allow.

Every name, account number and ID here is made up."""

import io
import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User
from app.permissions import APP_KEYS, APP_LABELS
from app.routers import hsbc_tradepay as routes
from app.services.hsbc_tradepay import erp, file_format as fmt, service
from app.services.hsbc_tradepay.models import HsbcTradepayFile, HsbcTradepayLine, HsbcTradepaySettings

HSBC_ID = "TESTID000000000001"
TODAY = date(2026, 11, 27)


def bank(account, ifsc="TEST0000001", status="approved", deleted="0", location="HO", name="ACME CEMENT PRIVATE LIMITED"):
    return {"account_no": account, "ifsc": ifsc, "status": status, "deleted": deleted, "location_code": location,
            "beneficiary": name, "bank": "TEST BANK", "branch": "MAIN"}


def erp_data():
    data = erp.ErpData()
    data.suppliers = {
        "100": {"vendor_id": 1, "vendor_code": "100", "vendor_name": "Acme Cement Private Limited",
                "vendor_type": "SUPPLIER-CEMENT", "enabled_flag": "Y", "end_date_active": None,
                "hold_all_payments_flag": "N"},
        "200": {"vendor_id": 2, "vendor_code": "200", "vendor_name": "Two Accounts Traders", "vendor_type": None,
                "enabled_flag": "Y", "end_date_active": None, "hold_all_payments_flag": "Y"},
        "300": {"vendor_id": 3, "vendor_code": "300", "vendor_name": "No Bank Works", "vendor_type": "SUPPLIER EXPENSE",
                "enabled_flag": "Y", "end_date_active": None, "hold_all_payments_flag": "N"},
    }
    data.bank_rows = {
        "100": [bank(" 000111222333 ", "test0000001 "), bank("000111222333", location="MU"),
                bank("999999999999", status="disabled"), bank("888888888888", deleted="1")],
        "200": [bank("1234567890", name="Two Accounts Traders"), bank("5555566666", "TEST0000002",
                                                                      name="Someone Else Holdings")],
        "300": [bank("7777777777", status="disabled", name="No Bank Works")],
    }
    data.sites = {
        1: [{"vendor_site_id": 11, "city": "PUNE-1", "pay_site_flag": "Y", "inactive_date": None},
            {"vendor_site_id": 12, "city": "MUMBAI", "pay_site_flag": "Y", "inactive_date": None}],
        2: [{"vendor_site_id": 21, "city": "NAVI MUMBAI", "pay_site_flag": "Y", "inactive_date": None}],
    }
    data.invoices = {(1, "INV/1"): [{
        "invoice_id": 501, "invoice_num": "INV/1", "invoice_date": datetime(2026, 11, 1), "invoice_amount": 1180,
        "amount_paid": 0, "payment_status_flag": "N", "cancelled_date": None, "vendor_site_id": 12, "currency": "INR",
    }]}
    return data


def payment(**change):
    row = {"vendor_code": "100", "invoice_no": "INV/1", "amount": "1,000.50", "party_name": "Acme Cement Pvt Ltd",
           "city": "Mumbai", "description": "Cement", "account_key": "000111222333|TEST0000001",
           "manual_account": "", "manual_ifsc": ""}
    return {**row, **change}


class FileLayoutTests(unittest.TestCase):
    def test_every_line_has_the_19_columns_of_the_template_and_only_the_yellow_cells_change(self):
        text = fmt.build_csv("RDCCHYD01", HSBC_ID, TODAY, [fmt.Payment(
            "INV/1", Decimal("125000.00"), "ACME CEMENT LLP", "Mumbai", "000111222333", "TEST0000001", "Raw Material")])
        self.assertEqual(text, (
            "H,TWC,RDCCHYD01,IN,TESTID000000000001,V,,Y,,,,,,,,,,,\r\n"
            "BH,RDCCHYD01,,INR,,,,,,,,,,,,,,,\r\n"
            "DT,INV/1,20261127,INR,125000,ACME CEMENT LLP,Mumbai,IN,Mumbai,Mumbai,INIFSCCD,,IN,,,"
            "000111222333,,TEST0000001,Raw Material\r\n"))

    def test_the_account_number_keeps_its_leading_zeros_and_gets_no_apostrophe(self):
        self.assertNotIn("'", fmt.build_csv("RDCCHO01", HSBC_ID, TODAY, [fmt.Payment(
            "1", Decimal("1"), "A", "Pune", "00012345", "TEST0000001", "Expense")]))

    def test_the_file_code(self):
        self.assertEqual(fmt.file_code("hyd", 1), "RDCCHYD01")
        self.assertEqual(fmt.file_code("HO", "3"), "RDCCHO03")
        self.assertEqual(fmt.file_code("MUM", 120), "RDCCMUM120")
        for location, series in (("H", 1), ("HYDE", 1), ("H1", 1), ("HYD", 0), ("HYD", 1000), ("HYD", "x")):
            with self.assertRaises(fmt.Invalid):
                fmt.file_code(location, series)

    def test_amounts(self):
        self.assertEqual(fmt.format_amount(fmt.amount("1,25,000.00")), "125000")
        self.assertEqual(fmt.format_amount(fmt.amount("Rs. 12.50")), "12.5")
        self.assertEqual(fmt.format_amount(fmt.amount(0.05)), "0.05")
        for bad in ("", "abc", "0", "-5", "1.005", "1e5", None):
            with self.assertRaises(fmt.Invalid, msg=repr(bad)):
                fmt.amount(bad)

    def test_the_party_name_loses_special_characters_and_fits_35(self):
        self.assertEqual(fmt.clean_party_name("M/s. A.B. Traders & Co."), ("A B Traders and Co", None))
        self.assertEqual(fmt.clean_party_name("Msquare Builders")[0], "Msquare Builders")
        self.assertEqual(fmt.clean_party_name("SHREE GANESH CONSTRUCTION MATERIALS PRIVATE LIMITED"),
                         ("SHREE GANESH CONSTN MATERIALS PVT", "cut"))
        self.assertEqual(fmt.clean_party_name("Acme Building Materials Private Limited"),
                         ("Acme Building Materials Pvt Ltd", "shortened"))
        name, how = fmt.clean_party_name("Abcdefghij " * 6)
        self.assertEqual(how, "cut")
        self.assertLessEqual(len(name), 35)
        self.assertFalse(name.endswith(" "))

    def test_city_description_and_invoice_number(self):
        self.assertEqual(fmt.clean_city("BANGALORE-5"), "Bangalore")
        self.assertEqual(fmt.clean_city("NAVI MUMBAI"), "Navi Mumbai")
        self.assertEqual(fmt.clean_description('Raw, "Material"\n'), "Raw Material")
        self.assertEqual(fmt.invoice_number("  RDC/26-27/001 "), "RDC/26-27/001")
        for bad in ("", "1,2", 'a"b', "=1+1", "+5", "@x", "-1", "x" * 51, "a\nb"):
            with self.assertRaises(fmt.Invalid, msg=repr(bad)):
                fmt.invoice_number(bad)


class ReviewTests(unittest.TestCase):
    def review(self, *entries, **kwargs):
        return service.review([dict(zip(("vendor_code", "invoice_no", "amount"), e)) for e in entries], erp_data(),
                              today=TODAY, **kwargs)

    def test_a_clean_row_is_filled_from_the_erp(self):
        row, = self.review(("100.0", "inv/1", "1000"))
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["warnings"], [])
        self.assertEqual((row["party_name"], row["city"], row["description"]),
                         ("Acme Cement Private Limited", "Mumbai", "Cement"))     # the city of the invoice's own site
        self.assertEqual(row["account_key"], "000111222333|TEST0000001")          # only the approved account
        self.assertEqual(row["accounts"][0]["locations"], ["HO", "MU"])
        self.assertEqual(row["invoice"]["status"], "Unpaid")

    def test_unknown_vendor_and_bad_values_are_errors(self):
        row, other = self.review(("999", "1", "10"), ("100", "", "x"))
        self.assertIn("not in the ERP", row["errors"][0])
        self.assertEqual(len(other["errors"]), 2)

    def test_two_accounts_need_a_choice_and_everything_odd_is_said(self):
        row, = self.review(("200", "B-9", "10"))
        self.assertEqual(row["account_key"], "")
        text = " ".join(row["warnings"])
        for part in ("holds all payments", "not in the ERP for this vendor", "no supplier type",
                     "2 approved bank accounts", "another name: Someone Else Holdings"):
            self.assertIn(part, text)

    def test_no_approved_account_means_it_has_to_be_typed(self):
        row, = self.review(("300", "C1", "10"))
        self.assertTrue(row["manual_account"])
        self.assertEqual(row["accounts"], [])
        self.assertIn("disabled or deleted", " ".join(row["warnings"]))

    def test_paid_too_much_repeated_and_already_used_invoices_are_warned(self):
        data = erp_data()
        first, second = service.review(
            [{"vendor_code": "100", "invoice_no": "INV/1", "amount": "2000"}] * 2, data,
            {("100", "INV/1"): ["RDCCHO01 of 20-11-2026"]}, TODAY)
        for part in ("more than the 1180", "more than once", "already in file RDCCHO01"):
            self.assertIn(part, " ".join(first["warnings"]))
        data.invoices[(1, "INV/1")][0].update(amount_paid=1180, payment_status_flag="Y")
        row, = service.review([{"vendor_code": "100", "invoice_no": "INV/1", "amount": "5"}], data, today=TODAY)
        self.assertIn("already paid in full", " ".join(row["warnings"]))


class PrepareTests(unittest.TestCase):
    def test_a_reviewed_row_becomes_a_payment_with_the_erp_account(self):
        lines, problems = service.prepare([payment(manual_account="1111111111", manual_ifsc="TEST0000009")], erp_data())
        self.assertEqual(problems, [])
        vendor, source, line = lines[0]
        self.assertEqual((vendor, source, line.account, line.ifsc), ("100", "erp", "000111222333", "TEST0000001"))
        self.assertEqual(line.amount, Decimal("1000.50"))

    def test_only_an_approved_account_of_that_vendor_is_accepted(self):
        for key in ("", "999999999999|TEST0000001", "888888888888|TEST0000001", "1234567890|TEST0000001"):
            lines, problems = service.prepare([payment(account_key=key)], erp_data())
            self.assertEqual(lines, [], key)
            self.assertIn("bank account", problems[0])

    def test_an_account_is_typed_only_when_the_erp_has_none(self):
        row = payment(vendor_code="300", account_key="", manual_account="12 34567890", manual_ifsc="test0000003")
        lines, problems = service.prepare([row], erp_data())
        self.assertEqual(problems, [])
        self.assertEqual((lines[0][1], lines[0][2].account, lines[0][2].ifsc), ("typed", "1234567890", "TEST0000003"))
        _, problems = service.prepare([{**row, "manual_ifsc": "TEST1000003"}], erp_data())
        self.assertIn("IFSC", problems[0])

    def test_values_the_file_may_not_hold_are_reported(self):
        _, problems = service.prepare([payment(party_name="A & B", city="", description=""),
                                       payment(party_name="x" * 36)], erp_data())
        self.assertEqual(len(problems), 4)


class RouteTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[
            User.__table__, HsbcTradepaySettings.__table__, HsbcTradepayFile.__table__, HsbcTradepayLine.__table__])
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()
        self.addCleanup(self.db.close)
        self.maker = User(id=1, email="maker@rdc.in", password_hash="x", role="user", first_name="Maker")
        self.other = User(id=2, email="other@rdc.in", password_hash="x", role="user")
        self.admin = User(id=3, email="admin@rdc.in", password_hash="x", role="admin")
        self.db.add_all([self.maker, self.other, self.admin])
        self.db.commit()
        patcher = patch.object(erp, "fetch", return_value=erp_data())
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_id(self):
        routes.save_settings(routes.SettingsIn(hsbc_id=f" {HSBC_ID.lower()} "), admin=self.admin, db=self.db)

    def make(self, series=1, user=None, **change):
        body = routes.GenerateIn(location="hyd", series=series, rows=[payment(**change)])
        return routes.generate(body, user=user or self.maker, db=self.db)

    def listing(self, user, q=""):
        return routes.files(q=q, page=1, page_size=10, user=user, db=self.db)

    def test_the_application_is_registered(self):
        self.assertIn("hsbc-tradepay", APP_KEYS)
        self.assertEqual(APP_LABELS["hsbc-tradepay"], "RDC HSBC Tradepay CSV Generation")

    def test_nothing_is_made_until_the_hsbc_id_is_set_and_only_an_admin_sees_it(self):
        with self.assertRaises(HTTPException) as caught:
            self.make()
        self.assertEqual(caught.exception.status_code, 409)
        self.set_id()
        self.assertEqual(routes.status(location="", user=self.admin, db=self.db)["hsbc_id"], HSBC_ID)
        seen = routes.status(location="", user=self.maker, db=self.db)
        self.assertEqual((seen["ready"], seen["hsbc_id"]), (True, ""))

    def test_a_file_is_made_recorded_and_its_code_cannot_be_used_twice(self):
        self.set_id()
        reply = self.make()
        self.assertEqual(reply.headers["content-disposition"], 'attachment; filename="RDCCHYD01.csv"')
        self.assertIn(b"H,TWC,RDCCHYD01,IN," + HSBC_ID.encode() + b",V,,Y", reply.body)
        line = self.db.query(HsbcTradepayLine).one()
        self.assertEqual((line.vendor_code, line.account_no, line.account_source), ("100", "000111222333", "erp"))
        with self.assertRaises(HTTPException) as caught:
            self.make()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("Use series number 2", caught.exception.detail)
        self.assertEqual(routes.status(location="HYD", user=self.maker, db=self.db)["next_series"], 2)
        found = routes.lookup(routes.LookupIn(rows=[{"vendor_code": "100", "invoice_no": "inv/1", "amount": "5"}]),
                              db=self.db)
        self.assertIn("already in file RDCCHYD01", " ".join(found["rows"][0]["warnings"]))

    def test_a_row_with_a_problem_makes_no_file(self):
        self.set_id()
        with self.assertRaises(HTTPException) as caught:
            self.make(account_key="")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.db.query(HsbcTradepayFile).count(), 0)

    def test_only_the_maker_or_an_admin_downloads_or_removes_and_removing_frees_the_code(self):
        self.set_id()
        made = self.make()
        item, = self.listing(self.other)["items"]
        self.assertFalse(item["mine"])
        for call in (routes.download, routes.remove):
            with self.assertRaises(HTTPException) as caught:
                call(item["id"], user=self.other, db=self.db)
            self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(routes.download(item["id"], user=self.admin, db=self.db).body, made.body)
        for text, count in (("hyd01", 1), ("inv/1", 1), ("100", 1), ("nothing", 0)):
            self.assertEqual(self.listing(self.maker, text)["total"], count, text)
        routes.remove(item["id"], user=self.maker, db=self.db)
        self.assertEqual(self.listing(self.maker)["total"], 0)
        self.assertEqual(self.db.query(HsbcTradepayFile).one().is_deleted, True)     # soft deleted, kept
        self.make()                                                                  # the code is free again
        self.assertEqual(self.listing(self.maker)["total"], 1)

    def test_an_unreachable_erp_is_said_plainly(self):
        with patch.object(erp, "fetch", side_effect=erp.ErpUnavailable("Could not connect to the Oracle ERP.")):
            with self.assertRaises(HTTPException) as caught:
                routes.lookup(routes.LookupIn(rows=[{"vendor_code": "1", "invoice_no": "1", "amount": "1"}]), db=self.db)
        self.assertEqual(caught.exception.status_code, 503)


def workbook(*rows, title="Payments", extra_first=False) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    if extra_first:
        sheet.title = "Cover"
        sheet.append(["Read me"])
        sheet = book.create_sheet()
    sheet.title = title
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def send(data: bytes, name="payments.xlsx"):
    return routes.upload(UploadFile(io.BytesIO(data), filename=name))


class InputSheetTests(unittest.TestCase):
    def refused(self, data, text, name="payments.xlsx", status=400):
        with self.assertRaises(HTTPException) as caught:
            send(data, name)
        self.assertEqual(caught.exception.status_code, status)
        self.assertIn(text, caught.exception.detail)

    def test_the_template_keeps_what_is_typed_and_is_read_back(self):
        reply = routes.template()
        self.assertIn("HSBC_Tradepay_Input_Template.xlsx", reply.headers["content-disposition"])
        book = openpyxl.load_workbook(io.BytesIO(reply.body))
        sheet = book["Payments"]
        self.assertEqual([c.value for c in sheet[1]], ["Vendor Code", "Invoice No.", "Amount (net of TDS)"])
        for cell in ("A2", "B2", "A501", "B501"):
            self.assertEqual(sheet[cell].number_format, "@", cell)
        self.refused(reply.body, "No payments were found")
        for row, values in ((2, ("00100", "007/1-2", 125000)), (4, ("200", "INV 2", 10.5))):
            for column, value in enumerate(values, 1):
                sheet.cell(row=row, column=column, value=value)
        buffer = io.BytesIO()
        book.save(buffer)
        self.assertEqual(send(buffer.getvalue()), {"rows": [
            {"vendor_code": "00100", "invoice_no": "007/1-2", "amount": "125000"},
            {"vendor_code": "200", "invoice_no": "INV 2", "amount": "10.5"},
        ], "notes": []})

    def test_columns_are_found_by_heading_wherever_they_are(self):
        data = workbook(["Payments of the week"], [], ["Amt", "Remarks", "Invoice Number", "Supplier Code"],
                        ["1,25,000.00", "x", " A-1 ", 100], [None, "only a remark", None, None],
                        [125000.00000000001, None, 4512, 200.0], title="Sheet1", extra_first=False)
        reply = send(data)
        self.assertEqual(reply["rows"], [
            {"vendor_code": "100", "invoice_no": "A-1", "amount": "1,25,000.00"},
            {"vendor_code": "200", "invoice_no": "4512", "amount": "125000"},
        ])
        self.assertEqual(reply["notes"], [])

    def test_the_payments_sheet_is_preferred_over_the_first_sheet(self):
        data = workbook(["Vendor Code", "Invoice No.", "Amount"], ["100", "A", 5], extra_first=True)
        self.assertEqual(len(send(data)["rows"]), 1)

    def test_cells_excel_changed_are_left_blank_and_reported(self):
        reply = send(workbook(["Vendor Code", "Invoice No.", "Amount"], ["100", datetime(2026, 1, 2), 5],
                              ["100", 12.5, datetime(2026, 1, 2)], ["100", "B", 10.125]))
        self.assertEqual([(r["invoice_no"], r["amount"]) for r in reply["rows"]],
                         [("", "5"), ("", ""), ("B", "10.125")])          # extra decimals reach the check, which refuses
        self.assertEqual(len(reply["notes"]), 3)
        self.assertIn("Row 2 of the sheet: Excel holds the invoice number as a date", reply["notes"][0])

    def test_files_that_could_be_read_wrongly_are_refused(self):
        self.refused(workbook(["Vendor Code", "Invoice No."], ["1", "2"]), "needs the headings")
        self.refused(workbook(["Vendor Code", "Invoice No.", "Amount", "Net Amount"], ["1", "2", 3, 4]),
                     "more than one amount column")
        self.refused(b"Vendor Code,Invoice No.,Amount\n1,2,3\n", "Upload an Excel file", name="payments.csv")
        many = [["1", str(n), 1] for n in range(service.MAX_ROWS + 1)]
        self.refused(workbook(["Vendor Code", "Invoice No.", "Amount"], *many), "more than 500 payments")
        self.refused(b"0" * (5 * 1024 * 1024 + 1), "over 5 MB", status=413)


class ErpQueryTests(unittest.TestCase):
    def test_each_list_becomes_bind_variables(self):
        seen = {}

        class Cursor:
            description = [("VENDOR_CODE",)]

            def execute(self, sql, binds):
                seen.update(sql=sql, binds=binds)

            def fetchall(self):
                return [("1",)]

        rows = erp._select(Cursor(), "x IN ({values}) AND y IN ({numbers})", values=[1, 2], numbers=["a'--"])
        self.assertEqual(rows, [{"vendor_code": "1"}])
        self.assertEqual(seen["sql"], "x IN (:values0,:values1) AND y IN (:numbers0)")
        self.assertEqual(seen["binds"], {"values0": 1, "values1": 2, "numbers0": "a'--"})

    def test_invoices_are_looked_up_as_typed_then_ignoring_capitals(self):
        calls = []

        def select(cursor, sql, values, numbers):
            calls.append((("UPPER" in sql), values, numbers))
            if "UPPER" not in sql:
                return []
            return [{"invoice_id": 1, "vendor_id": 7, "invoice_num": "Ab-1"}]

        data = erp.ErpData(suppliers={"700": {"vendor_id": 7}})
        with patch.object(erp, "_select", select):
            erp._fetch_invoices(None, data, [("700", "ab-1"), ("999", "z")])
        self.assertEqual(calls, [(False, [7], ["ab-1"]), (True, [7], ["AB-1"])])
        self.assertEqual(list(data.invoices), [(7, "AB-1")])


if __name__ == "__main__":
    unittest.main()
