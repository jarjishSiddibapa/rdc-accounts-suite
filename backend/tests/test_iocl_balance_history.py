import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models import IoclBalanceCheck, IoclBalanceNotification
from app.routers.iocl_balance import (
    PUBLIC_ISSUE_MESSAGE,
    _status_dict,
    cancel,
    get_checks as _get_checks,
    get_notifications as _get_notifications,
    job_status,
)


ADMIN = SimpleNamespace(id=1, role="admin")
USER = SimpleNamespace(id=2, role="user")


def get_checks(**kwargs):
    return _get_checks(**kwargs, current_user=ADMIN)


def get_notifications(**kwargs):
    return _get_notifications(**kwargs, current_user=ADMIN)


# IoclBalanceNotification.body/subject use MySQL's LONGTEXT, which SQLite's
# in-memory test engine (used here, same as elsewhere in this suite) can't
# compile DDL for directly - treat it as plain TEXT for sqlite only, leaving
# the real MySQL schema untouched.
@compiles(LONGTEXT, "sqlite")
def _longtext_as_text_on_sqlite(element, compiler, **kw):
    return "TEXT"


class IoclBalanceCheckHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        IoclBalanceCheck.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        self.db.add_all(
            [
                IoclBalanceCheck(
                    id=1, trigger="scheduled", status="success",
                    balance=Decimal("1245620.60"), error_message=None,
                    checked_at=datetime(2026, 8, 27, 6, 0), duration_seconds=4.2,
                    is_deleted=False,
                ),
                IoclBalanceCheck(
                    id=2, trigger="manual", status="error",
                    balance=None, error_message="Login timed out after 60s",
                    checked_at=datetime(2026, 8, 27, 6, 30), duration_seconds=61.0,
                    is_deleted=False,
                ),
                IoclBalanceCheck(
                    id=3, trigger="scheduled", status="skipped",
                    balance=None, error_message="Another check is already in progress",
                    checked_at=datetime(2026, 8, 27, 7, 0), duration_seconds=0.1,
                    is_deleted=False,
                ),
                IoclBalanceCheck(
                    id=4, trigger="scheduled", status="success",
                    balance=Decimal("1200000.00"), error_message=None,
                    checked_at=datetime(2026, 8, 27, 7, 30), duration_seconds=3.9,
                    is_deleted=False,
                ),
                # Soft-deleted rows must never surface in the history API,
                # matching the suite-wide soft-delete convention.
                IoclBalanceCheck(
                    id=5, trigger="manual", status="success",
                    balance=Decimal("999999.00"), error_message=None,
                    checked_at=datetime(2026, 8, 27, 8, 0), duration_seconds=3.5,
                    is_deleted=True,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_default_order_is_most_recent_first_and_excludes_soft_deleted(self):
        result = get_checks(limit=20, offset=0, status=None, trigger=None, db=self.db)
        self.assertEqual(result["total"], 4)
        self.assertEqual([row["id"] for row in result["items"]], [4, 3, 2, 1])

    def test_pagination_limit_and_offset(self):
        page1 = get_checks(limit=2, offset=0, status=None, trigger=None, db=self.db)
        page2 = get_checks(limit=2, offset=2, status=None, trigger=None, db=self.db)
        self.assertEqual(page1["total"], 4)
        self.assertEqual([row["id"] for row in page1["items"]], [4, 3])
        self.assertEqual([row["id"] for row in page2["items"]], [2, 1])

    def test_limit_is_clamped_to_a_sane_range(self):
        result = get_checks(limit=10_000, offset=0, status=None, trigger=None, db=self.db)
        self.assertEqual(len(result["items"]), 4)  # clamped, but only 4 rows exist anyway
        result_negative_offset = get_checks(limit=20, offset=-5, status=None, trigger=None, db=self.db)
        self.assertEqual(result_negative_offset["total"], 4)

    def test_filter_by_status(self):
        result = get_checks(limit=20, offset=0, status="error", trigger=None, db=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], 2)
        self.assertEqual(result["items"][0]["error_message"], "Login timed out after 60s")

    def test_regular_user_sees_safe_error_while_admin_sees_diagnostics(self):
        user_result = _get_checks(
            limit=20, offset=0, status="error", trigger=None,
            db=self.db, current_user=USER,
        )
        admin_result = get_checks(limit=20, offset=0, status="error", trigger=None, db=self.db)

        self.assertEqual(user_result["items"][0]["error_message"], PUBLIC_ISSUE_MESSAGE)
        self.assertEqual(admin_result["items"][0]["error_message"], "Login timed out after 60s")

    def test_filter_by_trigger(self):
        result = get_checks(limit=20, offset=0, status=None, trigger="manual", db=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], 2)

    def test_combined_status_and_trigger_filters(self):
        result = get_checks(limit=20, offset=0, status="success", trigger="scheduled", db=self.db)
        self.assertEqual(sorted(row["id"] for row in result["items"]), [1, 4])

    def test_unknown_filter_values_are_ignored_not_erroring(self):
        result = get_checks(limit=20, offset=0, status="not-a-real-status", trigger=None, db=self.db)
        self.assertEqual(result["total"], 4)

    def test_row_shape_includes_balance_duration_and_ist_timestamp(self):
        result = get_checks(limit=20, offset=0, status=None, trigger=None, db=self.db)
        row = next(r for r in result["items"] if r["id"] == 1)
        self.assertEqual(row["balance"], 1245620.60)
        self.assertEqual(row["duration_seconds"], 4.2)
        self.assertIsNotNone(row["checked_at"])
        self.assertTrue(row["checked_at"].endswith(("+05:30", "+5:30")) or "+05:30" in row["checked_at"])


class IoclBalanceNotificationHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        IoclBalanceNotification.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        self.db.add_all(
            [
                IoclBalanceNotification(
                    id=1, notification_key="daily-2026-08-27", check_id=1,
                    notification_type="daily", threshold_amount=None,
                    balance=Decimal("1245620.60"),
                    subject="Morning IOCL CCMS balance", body="<p>body</p>",
                    to_recipients="a@rdc.in", cc_recipients=None,
                    status="sent", error_message=None,
                    created_at=datetime(2026, 8, 27, 8, 0), attempted_at=datetime(2026, 8, 27, 8, 0, 5),
                    sent_at=datetime(2026, 8, 27, 8, 0, 6), is_deleted=False,
                ),
                IoclBalanceNotification(
                    id=2, notification_key="threshold-500000-2026-08-27", check_id=2,
                    notification_type="threshold", threshold_amount=Decimal("500000.00"),
                    balance=Decimal("450000.00"),
                    subject="IOCL CCMS balance below Rs 5,00,000", body="<p>body</p>",
                    to_recipients="a@rdc.in", cc_recipients="b@rdc.in",
                    status="failed", error_message="SMTP auth failed",
                    created_at=datetime(2026, 8, 27, 9, 0), attempted_at=datetime(2026, 8, 27, 9, 0, 2),
                    sent_at=None, is_deleted=False,
                ),
                IoclBalanceNotification(
                    id=3, notification_key="daily-2026-08-26", check_id=None,
                    notification_type="daily", threshold_amount=None,
                    balance=Decimal("1300000.00"),
                    subject="Morning IOCL CCMS balance", body="<p>body</p>",
                    to_recipients="a@rdc.in", cc_recipients=None,
                    status="pending", error_message=None,
                    created_at=datetime(2026, 8, 26, 8, 0), attempted_at=None,
                    sent_at=None, is_deleted=False,
                ),
                IoclBalanceNotification(
                    id=4, notification_key="daily-2026-08-25", check_id=None,
                    notification_type="daily", threshold_amount=None,
                    balance=Decimal("1000000.00"),
                    subject="Morning IOCL CCMS balance", body="<p>body</p>",
                    to_recipients="a@rdc.in", cc_recipients=None,
                    status="sent", error_message=None,
                    created_at=datetime(2026, 8, 25, 8, 0), attempted_at=datetime(2026, 8, 25, 8, 0, 3),
                    sent_at=datetime(2026, 8, 25, 8, 0, 4), is_deleted=True,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_default_order_is_most_recent_first_and_excludes_soft_deleted(self):
        result = get_notifications(limit=20, offset=0, notification_type=None, status=None, db=self.db)
        self.assertEqual(result["total"], 3)
        self.assertEqual([row["id"] for row in result["items"]], [2, 1, 3])

    def test_pagination_limit_and_offset(self):
        page1 = get_notifications(limit=1, offset=0, notification_type=None, status=None, db=self.db)
        page2 = get_notifications(limit=1, offset=1, notification_type=None, status=None, db=self.db)
        self.assertEqual(page1["total"], 3)
        self.assertEqual(page1["items"][0]["id"], 2)
        self.assertEqual(page2["items"][0]["id"], 1)

    def test_filter_by_notification_type(self):
        result = get_notifications(limit=20, offset=0, notification_type="threshold", status=None, db=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], 2)
        self.assertEqual(result["items"][0]["threshold_amount"], 500000.00)

    def test_filter_by_status(self):
        result = get_notifications(limit=20, offset=0, notification_type=None, status="failed", db=self.db)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], 2)
        self.assertEqual(result["items"][0]["error_message"], "SMTP auth failed")

    def test_regular_user_cannot_read_mail_delivery_diagnostics(self):
        result = _get_notifications(
            limit=20, offset=0, notification_type=None, status="failed",
            db=self.db, current_user=USER,
        )
        self.assertEqual(result["items"][0]["error_message"], PUBLIC_ISSUE_MESSAGE)

    def test_pending_notification_has_null_sent_at(self):
        result = get_notifications(limit=20, offset=0, notification_type=None, status="pending", db=self.db)
        self.assertEqual(result["total"], 1)
        self.assertIsNone(result["items"][0]["sent_at"])

    def test_row_shape_includes_balance_and_subject(self):
        result = get_notifications(limit=20, offset=0, notification_type="daily", status="sent", db=self.db)
        self.assertEqual(result["total"], 1)
        row = result["items"][0]
        self.assertEqual(row["id"], 1)
        self.assertEqual(row["balance"], 1245620.60)
        self.assertEqual(row["subject"], "Morning IOCL CCMS balance")
        self.assertIsNotNone(row["sent_at"])


class IoclJobErrorVisibilityTests(unittest.TestCase):
    def test_status_summary_hides_error_from_regular_users_only(self):
        settings = SimpleNamespace(
            enabled=True,
            check_interval_minutes=30,
            username="ULTRAFINE",
            password_encrypted="cipher",
            sender_email="sender@example.test",
            sender_app_password_encrypted="cipher",
            session_state_encrypted=None,
            last_balance=None,
            last_checked_at=None,
            last_check_status="error",
            last_error="readonly field timeout",
            next_check_at=None,
        )

        self.assertEqual(
            _status_dict(settings, reveal_errors=False)["last_error"],
            PUBLIC_ISSUE_MESSAGE,
        )
        self.assertEqual(
            _status_dict(settings, reveal_errors=True)["last_error"],
            "readonly field timeout",
        )

    def test_regular_user_job_error_is_safe(self):
        raw = {"id": "job-1", "status": "error", "error": "portal stack trace"}
        with patch("app.routers.iocl_balance.get_job", return_value=raw):
            result = job_status("job-1", user=USER)
        self.assertEqual(result["error"], PUBLIC_ISSUE_MESSAGE)

    def test_admin_job_error_keeps_technical_details(self):
        raw = {"id": "job-1", "status": "error", "error": "portal stack trace"}
        with patch("app.routers.iocl_balance.get_job", return_value=raw):
            result = job_status("job-1", user=ADMIN)
        self.assertEqual(result["error"], "portal stack trace")

    def test_cancel_response_cannot_leak_terminal_error(self):
        raw = {"id": "job-1", "status": "error", "error": "portal stack trace"}
        with patch("app.routers.iocl_balance.cancel_job", return_value=raw):
            result = cancel("job-1", user=USER)
        self.assertEqual(result["error"], PUBLIC_ISSUE_MESSAGE)


if __name__ == "__main__":
    unittest.main()
