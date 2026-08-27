import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models import IoclBalanceSettings, SystemEmailSettings, User
from app.routers.iocl_balance import SettingsBody, TestMailBody, put_settings, send_test_mail
from app.services.iocl_balance.monitor import _send_from_configured_sender
from fastapi import HTTPException


# IoclBalanceSettings.session_state_encrypted/daily_body_template/etc use
# MySQL's LONGTEXT, which SQLite's in-memory test engine can't compile DDL
# for directly - treat it as plain TEXT for sqlite only (see
# test_iocl_balance_history.py for the same shim).
@compiles(LONGTEXT, "sqlite")
def _longtext_as_text_on_sqlite(element, compiler, **kw):
    return "TEXT"


def _settings_body(**overrides) -> SettingsBody:
    values = dict(
        version=1,
        enabled=False,
        login_url="https://beta.iocxtrapower.com/account/login",
        username="",
        password=None,
        login_timeout_seconds=60,
        check_interval_minutes=30,
        daily_email_enabled=True,
        daily_email_time="08:00",
        daily_to=["a@rdc.in"],
        daily_cc=[],
        daily_subject_template="IOCL Balance as on {date}",
        daily_body_template="Balance is {balance}",
        alerts_enabled=True,
        alert_start_amount=Decimal("500000"),
        alert_step_amount=Decimal("50000"),
        alert_to=["a@rdc.in"],
        alert_cc=[],
        alert_subject_template="Alert - balance below {threshold}",
        alert_body_template="Balance is {balance}, threshold {threshold}",
    )
    values.update(overrides)
    return SettingsBody(**values)


class PutSettingsSenderAssignmentTests(unittest.TestCase):
    """This is a shared, multi-user automation - whoever saves the settings
    becomes the mail sender automatically (their own Gmail identity from
    their Settings page). There is no picker letting one user choose to
    send as somebody else."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        IoclBalanceSettings.__table__.create(self.engine)
        User.__table__.create(self.engine)
        SystemEmailSettings.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        self.db.add_all(
            [
                User(id=1, email="zara@rdc.in", first_name="Zara", last_name="Iyer", password_hash="x", role="user", is_active=True, is_deleted=False),
                User(id=2, email="amit@rdc.in", first_name="Amit", last_name="Sharma", password_hash="x", role="admin", is_active=True, is_deleted=False),
            ]
        )
        self.db.add(
            IoclBalanceSettings(
                id=1, enabled=False, login_url="https://beta.iocxtrapower.com/account/login?returnUrl=%2F",
                login_timeout_seconds=60, check_interval_minutes=30,
                daily_email_enabled=True, daily_email_time="08:00",
                daily_subject_template="IOCL Balance as on {date}",
                daily_body_template="Balance is {balance}",
                alerts_enabled=True, alert_start_amount=Decimal("500000"), alert_step_amount=Decimal("50000"),
                alert_subject_template="Alert - balance below {threshold}",
                alert_body_template="Balance is {balance}, threshold {threshold}",
                updated_at=datetime(2026, 8, 27, 0, 0), version=1, is_deleted=False,
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _get_user(self, user_id: int) -> User:
        return self.db.query(User).filter(User.id == user_id).one()

    def test_saving_sets_sender_to_the_current_user(self):
        with patch("app.routers.iocl_balance.get_email_settings", return_value={"configured": True}):
            result = put_settings(_settings_body(version=1), db=self.db, current_user=self._get_user(1))

        self.assertEqual(result["sender_user_id"], 1)
        self.assertEqual(result["sender_email"], "zara@rdc.in")
        self.assertTrue(result["sender_configured"])

    def test_a_later_save_by_a_different_user_reassigns_the_sender(self):
        with patch("app.routers.iocl_balance.get_email_settings", return_value={"configured": True}):
            put_settings(_settings_body(version=1), db=self.db, current_user=self._get_user(1))
            result = put_settings(_settings_body(version=2), db=self.db, current_user=self._get_user(2))

        self.assertEqual(result["sender_user_id"], 2)
        self.assertEqual(result["sender_email"], "amit@rdc.in")

    def test_client_cannot_pick_a_different_sender_via_the_request_body(self):
        # SettingsBody has no sender_user_id field at all - even if a client
        # sent one, pydantic silently drops unknown fields, and the sender
        # is always taken from the authenticated caller instead.
        with patch("app.routers.iocl_balance.get_email_settings", return_value={"configured": True}):
            body = SettingsBody(**{**_settings_body(version=1).model_dump(), "sender_user_id": 2})
            result = put_settings(body, db=self.db, current_user=self._get_user(1))

        self.assertEqual(result["sender_user_id"], 1)


class SendTestMailTests(unittest.TestCase):
    """The "Send test mail" button previews an in-progress (possibly
    unsaved) template - it must always land in the current user's own
    inbox, never the real To/Cc distribution list."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        IoclBalanceSettings.__table__.create(self.engine)
        User.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.db.add(
            IoclBalanceSettings(
                id=1, enabled=False, login_url="https://beta.iocxtrapower.com/account/login?returnUrl=%2F",
                login_timeout_seconds=60, check_interval_minutes=30,
                daily_email_enabled=True, daily_email_time="08:00",
                daily_subject_template="IOCL Balance as on {date}",
                daily_body_template="Balance is {balance}",
                alerts_enabled=True, alert_start_amount=Decimal("500000"), alert_step_amount=Decimal("50000"),
                last_balance=Decimal("1245620.60"),
                alert_subject_template="Alert - balance below {threshold}",
                alert_body_template="Balance is {balance}, threshold {threshold}",
                updated_at=datetime(2026, 8, 27, 0, 0), version=1, is_deleted=False,
            )
        )
        self.db.add(User(id=1, email="zara@rdc.in", first_name="Zara", last_name="Iyer", password_hash="x", role="user", is_active=True, is_deleted=False))
        self.db.commit()
        self.user = self.db.query(User).filter(User.id == 1).one()

    def tearDown(self):
        self.db.close()

    def test_sends_the_rendered_preview_to_the_callers_own_inbox_only(self):
        sender = {"email": "zara@rdc.in", "app_password": "app-pass", "configured": True}
        body = TestMailBody(mail_type="daily", subject_template="Balance on {date}", body_template="It is {balance}")
        with patch("app.routers.iocl_balance.get_email_settings", return_value=sender), \
             patch("app.routers.iocl_balance.send_mail") as mock_send:
            result = send_test_mail(body, db=self.db, current_user=self.user)

        self.assertEqual(result, {"ok": True, "sent_to": "zara@rdc.in"})
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["to_addresses"], ["zara@rdc.in"])
        self.assertEqual(kwargs["cc_addresses"], [])
        self.assertIn("12,45,620.60", kwargs["html_body"])
        self.assertTrue(kwargs["subject"].startswith("[Test] "))

    def test_alert_preview_renders_the_configured_starting_threshold(self):
        sender = {"email": "zara@rdc.in", "app_password": "app-pass", "configured": True}
        body = TestMailBody(mail_type="alert", subject_template="Below {threshold}", body_template="Balance {balance}, threshold {threshold}")
        with patch("app.routers.iocl_balance.get_email_settings", return_value=sender), \
             patch("app.routers.iocl_balance.send_mail") as mock_send:
            send_test_mail(body, db=self.db, current_user=self.user)

        html_body = mock_send.call_args.kwargs["html_body"]
        self.assertIn("5 lakh", html_body)  # settings.alert_start_amount, human-readable

    def test_requires_the_caller_to_have_a_configured_sender(self):
        body = TestMailBody(mail_type="daily", subject_template="Balance on {date}", body_template="It is {balance}")
        with patch("app.routers.iocl_balance.get_email_settings", return_value={"configured": False}):
            with self.assertRaises(HTTPException) as ctx:
                send_test_mail(body, db=self.db, current_user=self.user)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_a_template_field_not_allowed_for_the_mail_type(self):
        sender = {"email": "zara@rdc.in", "app_password": "app-pass", "configured": True}
        body = TestMailBody(mail_type="daily", subject_template="Below {threshold}", body_template="It is {balance}")
        with patch("app.routers.iocl_balance.get_email_settings", return_value=sender):
            with self.assertRaises(HTTPException) as ctx:
                send_test_mail(body, db=self.db, current_user=self.user)
        self.assertEqual(ctx.exception.status_code, 400)


class SendFromConfiguredSenderTests(unittest.TestCase):
    def test_sends_from_the_configured_users_own_gmail_identity(self):
        sender = {"email": "zara@rdc.in", "app_password": "app-pass", "configured": True}
        with patch("app.services.mailer_shared.get_email_settings", return_value=sender), \
             patch("app.services.mailer_shared.send_mail") as mock_send:
            ok, message = _send_from_configured_sender(
                db=None, sender_user_id=1,
                to_emails=["ops@rdc.in"], cc_emails=[],
                subject="IOCL Balance as on 27-Aug-2026", body="Balance is 12,45,620.60",
            )

        self.assertTrue(ok)
        self.assertIn("zara@rdc.in", message)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["from_email"], "zara@rdc.in")

    def test_falls_back_to_system_email_when_sender_not_configured(self):
        with patch("app.services.mailer_shared.get_email_settings", return_value={"configured": False}), \
             patch("app.system_mailer.send_system_email_to_recipients", return_value=(True, "sent")) as mock_system:
            ok, message = _send_from_configured_sender(
                db="fake-db", sender_user_id=1,
                to_emails=["ops@rdc.in"], cc_emails=[],
                subject="subject", body="body",
            )

        self.assertTrue(ok)
        self.assertEqual(message, "sent")
        mock_system.assert_called_once_with("fake-db", ["ops@rdc.in"], [], "subject", "body")

    def test_falls_back_to_system_email_when_no_sender_configured_at_all(self):
        with patch("app.system_mailer.send_system_email_to_recipients", return_value=(True, "sent")) as mock_system:
            ok, message = _send_from_configured_sender(
                db="fake-db", sender_user_id=None,
                to_emails=["ops@rdc.in"], cc_emails=["cc@rdc.in"],
                subject="subject", body="body",
            )

        self.assertTrue(ok)
        mock_system.assert_called_once_with("fake-db", ["ops@rdc.in"], ["cc@rdc.in"], "subject", "body")

    def test_reports_soft_failure_when_send_mail_raises(self):
        sender = {"email": "zara@rdc.in", "app_password": "app-pass", "configured": True}
        with patch("app.services.mailer_shared.get_email_settings", return_value=sender), \
             patch("app.services.mailer_shared.send_mail", side_effect=RuntimeError("SMTP auth failed")):
            ok, message = _send_from_configured_sender(
                db=None, sender_user_id=1,
                to_emails=["ops@rdc.in"], cc_emails=[],
                subject="subject", body="body",
            )

        self.assertFalse(ok)
        self.assertEqual(message, "SMTP auth failed")


if __name__ == "__main__":
    unittest.main()
