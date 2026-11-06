"""IOCL Balance Monitor and Invoice Booking Tracker: send the mail now to people of your choice."""

import json
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import sessionmaker

from app import security
from app.database import Base
from app.models import (
    IoclBalanceCheck, IoclBalanceNotification, IoclBalanceSettings,
    InvoiceBookingTrackerCheck, InvoiceBookingTrackerMapping, InvoiceBookingTrackerNotification,
    InvoiceBookingTrackerSettings,
)
from app.routers import invoice_booking_tracker as tracker_routes
from app.routers import iocl_balance as iocl_routes
from app.services.invoice_booking_tracker import monitor as tracker
from app.services.iocl_balance import monitor as iocl


@compiles(LONGTEXT, "sqlite")
def _longtext_on_sqlite(element, compiler, **kw):   # the tests run on SQLite, production on MySQL
    return "TEXT"


@compiles(BigInteger, "sqlite")
def _bigint_on_sqlite(element, compiler, **kw):     # only an INTEGER primary key auto-numbers on SQLite
    return "INTEGER"


def _session(*models):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.__table__ for m in models])
    return sessionmaker(bind=engine)()


class _SessionFactory:
    """SessionLocal stand-in: every call opens a session on the same in-memory database."""

    def __init__(self, session):
        self._engine = session.get_bind()

    def __call__(self):
        return sessionmaker(bind=self._engine)()


class IoclManualMailTests(unittest.TestCase):
    def setUp(self):
        self.db = _session(IoclBalanceSettings, IoclBalanceCheck, IoclBalanceNotification)
        self.addCleanup(self.db.close)
        settings = iocl.get_or_create_settings(self.db)
        settings.sender_email = "iocl@rdc.in"
        settings.sender_app_password_encrypted = security.encrypt("app-password")
        settings.daily_to = json.dumps(["morning@rdc.in"])
        settings.daily_cc = json.dumps(["boss@rdc.in"])
        self.db.commit()
        patcher = patch.object(iocl, "SessionLocal", _SessionFactory(self.db))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _add_check(self, balance, status="success"):
        self.db.add(IoclBalanceCheck(trigger="manual", status=status, balance=balance, checked_at=datetime(2026, 11, 6, 4, 0)))
        self.db.commit()

    def test_defaults_start_from_the_saved_recipients_and_the_latest_balance(self):
        self._add_check(Decimal("1245620.60"))
        defaults = iocl.manual_mail_defaults(self.db)
        self.assertTrue(defaults["available"])
        self.assertEqual(defaults["to"], ["morning@rdc.in"])
        self.assertEqual(defaults["cc"], ["boss@rdc.in"])
        self.assertIn("IOCL Balance as on", defaults["subject"])
        self.assertIn("12,45,620.60", defaults["html_body"])

    def test_nothing_to_send_until_a_balance_has_been_read(self):
        self._add_check(None, status="error")
        defaults = iocl.manual_mail_defaults(self.db)
        self.assertFalse(defaults["available"])
        self.assertEqual(defaults["to"], ["morning@rdc.in"])
        with self.assertRaises(ValueError):
            iocl.send_manual_mail(self.db, ["x@rdc.in"], [], "Subject")

    def test_it_goes_to_the_people_chosen_and_does_not_count_as_the_morning_mail(self):
        self._add_check(Decimal("500000"))
        with patch("app.services.mailer_shared.send_mail") as sent:
            result = iocl.send_manual_mail(self.db, ["someone.else@rdc.in"], ["cc@rdc.in"], "IOCL balance for you")
        self.assertEqual(result["status"], "sent")
        kwargs = sent.call_args.kwargs
        self.assertEqual(kwargs["to_addresses"], ["someone.else@rdc.in"])
        self.assertEqual(kwargs["cc_addresses"], ["cc@rdc.in"])
        self.assertEqual(kwargs["subject"], "IOCL balance for you")
        self.assertEqual(kwargs["from_email"], "iocl@rdc.in")
        row = self.db.query(IoclBalanceNotification).one()
        self.assertEqual((row.notification_type, row.status), ("manual", "sent"))
        self.db.refresh(iocl.get_or_create_settings(self.db))
        self.assertIsNone(iocl.get_or_create_settings(self.db).last_daily_sent_date)

    def test_a_failed_send_is_reported_and_recorded(self):
        self._add_check(Decimal("500000"))
        with patch("app.services.mailer_shared.send_mail", side_effect=OSError("smtp down")):
            result = iocl.send_manual_mail(self.db, ["a@rdc.in"], [], "Subject")
        self.assertEqual(result["status"], "failed")
        self.db.expire_all()
        self.assertEqual(self.db.query(IoclBalanceNotification).one().status, "failed")

    def test_recipients_and_subject_are_checked(self):
        self._add_check(Decimal("1"))
        for to, subject in (([], "Subject"), (["a@rdc.in"], ""), (["a@rdc.in"], "two\nlines")):
            with self.assertRaises(ValueError):
                iocl.send_manual_mail(self.db, to, [], subject)

    def test_the_route_turns_a_bad_request_and_a_failed_send_into_errors(self):
        body = iocl_routes.SendMailBody(to=["a@rdc.in", "A@rdc.in"], cc=["a@rdc.in", "c@rdc.in"], subject="S")
        self.assertEqual(body.to, ["a@rdc.in"])
        user = type("U", (), {"id": 1})()
        with patch.object(iocl_routes.auth_limiter, "enforce"), \
                patch.object(iocl_routes.monitor, "send_manual_mail", side_effect=ValueError("no balance")):
            with self.assertRaises(HTTPException) as caught:
                iocl_routes.send_mail_now(body, self.db, user)
        self.assertEqual(caught.exception.status_code, 400)
        with patch.object(iocl_routes.auth_limiter, "enforce"), \
                patch.object(iocl_routes.monitor, "send_manual_mail", return_value={"status": "failed", "message": "smtp down"}):
            with self.assertRaises(HTTPException) as caught:
                iocl_routes.send_mail_now(body, self.db, user)
        self.assertEqual(caught.exception.status_code, 502)


class TrackerManualMailTests(unittest.TestCase):
    ROWS = [
        {"location": "Mumbai", "responsible_person": "Asha", "pending": 4, "records_scanned": 10, "pages_scanned": 1},
        {"location": "Delhi", "responsible_person": "Ravi", "pending": 0, "records_scanned": 5, "pages_scanned": 1},
    ]

    def setUp(self):
        self.db = _session(InvoiceBookingTrackerSettings, InvoiceBookingTrackerCheck,
                           InvoiceBookingTrackerNotification, InvoiceBookingTrackerMapping)
        self.addCleanup(self.db.close)
        settings = tracker.get_or_create_settings(self.db)
        settings.sender_email = "tracker@rdc.in"
        settings.sender_app_password_encrypted = security.encrypt("app-password")
        settings.mail_to = json.dumps(["scheduled@rdc.in"])
        settings.mail_cc = json.dumps([])
        self.db.add(InvoiceBookingTrackerCheck(
            trigger="manual", status="success", total_pending=4, total_records_scanned=15, total_pages_scanned=2,
            result_json=json.dumps(self.ROWS), checked_at=datetime(2026, 11, 6, 4, 0),
        ))
        self.db.commit()
        patcher = patch.object(tracker, "SessionLocal", _SessionFactory(self.db))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_start_from_the_saved_recipients_and_show_the_mail(self):
        defaults = tracker_routes.mail_defaults(self.db)
        self.assertTrue(defaults["available"])
        self.assertEqual(defaults["to"], ["scheduled@rdc.in"])
        self.assertIn("6th November 2026", defaults["subject"])
        self.assertIn("Mumbai", defaults["html_body"])

    def test_it_goes_to_the_people_chosen_and_does_not_count_as_the_scheduled_mail(self):
        check, rows = tracker_routes._latest_complete_check(self.db)
        with patch.object(tracker, "send_mail") as sent:
            result = tracker.send_manual_mail(self.db, check, rows, ["other@rdc.in"], ["cc@rdc.in"], "Tracker for you")
        self.assertEqual(result["status"], "sent")
        kwargs = sent.call_args.kwargs
        self.assertEqual(kwargs["to_addresses"], ["other@rdc.in"])
        self.assertEqual(kwargs["cc_addresses"], ["cc@rdc.in"])
        self.assertEqual(kwargs["subject"], "Tracker for you")
        self.assertIn("Mumbai", kwargs["html_body"])
        self.assertTrue(self.db.query(InvoiceBookingTrackerNotification).one().notification_key.startswith("manual:"))
        self.assertIsNone(tracker.get_or_create_settings(self.db).last_scheduled_sent_date)

    def test_a_scheduled_mail_still_counts_as_the_days_mail(self):
        check, rows = tracker_routes._latest_complete_check(self.db)
        self.db.add(InvoiceBookingTrackerNotification(
            notification_key="scheduled:2026-11-06", check_id=check.id, subject="S", body="B",
            to_recipients=json.dumps(["a@rdc.in"]), cc_recipients="[]", result_json="[]",
            attachment_filename="x.xlsx", status="pending",
        ))
        self.db.commit()
        notification_id = self.db.query(InvoiceBookingTrackerNotification).one().id
        with patch.object(tracker, "send_mail"):
            self.assertEqual(tracker._deliver_notification(notification_id)["status"], "sent")
        self.db.expire_all()
        self.assertIsNotNone(tracker.get_or_create_settings(self.db).last_scheduled_sent_date)

    def test_nothing_to_send_without_a_complete_check_and_bad_input_is_refused(self):
        check, rows = tracker_routes._latest_complete_check(self.db)
        for to, subject in (([], "Subject"), (["a@rdc.in"], ""), (["a@rdc.in"], "two\nlines")):
            with self.assertRaises(ValueError):
                tracker.send_manual_mail(self.db, check, rows, to, [], subject)
        self.db.query(InvoiceBookingTrackerCheck).delete()
        self.db.commit()
        self.assertFalse(tracker_routes.mail_defaults(self.db)["available"])
        user = type("U", (), {"id": 1})()
        body = tracker_routes.SendMailBody(to=["a@rdc.in"], subject="S")
        with patch.object(tracker_routes.auth_limiter, "enforce"):
            with self.assertRaises(HTTPException) as caught:
                tracker_routes.send_mail_now(body, self.db, user)
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
