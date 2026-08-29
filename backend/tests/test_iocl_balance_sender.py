import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models import IoclBalanceSettings, User
from app.routers.iocl_balance import SettingsBody, TestMailBody, put_settings, send_test_mail
from app.services.iocl_balance.monitor import _send_from_configured_sender


@compiles(LONGTEXT, "sqlite")
def _longtext_as_text_on_sqlite(element, compiler, **kw):
    return "TEXT"


def _settings_body(**overrides) -> SettingsBody:
    values = dict(
        version=1,
        enabled=False,
        login_url="https://beta.iocxtrapower.com/account/login",
        username="portal-user",
        password=None,
        sender_email="alerts@rdc.in",
        sender_app_password=None,
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
        alert_repeat_hours=30,
        alert_to=["a@rdc.in"],
        alert_cc=[],
        alert_subject_template="Alert - balance below {threshold}",
        alert_body_template="Balance is {balance}, threshold {threshold}",
    )
    values.update(overrides)
    return SettingsBody(**values)


def _settings(**overrides) -> IoclBalanceSettings:
    values = dict(
        id=1,
        enabled=False,
        login_url="https://beta.iocxtrapower.com/account/login?returnUrl=%2F",
        username="portal-user",
        password_encrypted="portal-cipher",
        sender_email="alerts@rdc.in",
        sender_app_password_encrypted="mail-cipher",
        login_timeout_seconds=60,
        check_interval_minutes=30,
        daily_email_enabled=True,
        daily_email_time="08:00",
        daily_subject_template="IOCL Balance as on {date}",
        daily_body_template="Balance is {balance}",
        alerts_enabled=True,
        alert_start_amount=Decimal("500000"),
        alert_step_amount=Decimal("50000"),
        alert_repeat_hours=30,
        alert_subject_template="Alert - balance below {threshold}",
        alert_body_template="Balance is {balance}, threshold {threshold}",
        updated_at=datetime(2026, 8, 27, 0, 0),
        version=1,
        is_deleted=False,
    )
    values.update(overrides)
    return IoclBalanceSettings(**values)


class PutSettingsSenderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        User.__table__.create(self.engine)
        IoclBalanceSettings.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.db.add(_settings())
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_admin_owned_sender_is_saved_without_a_user_owner(self):
        with patch("app.routers.iocl_balance.security.encrypt", return_value="new-cipher"):
            result = put_settings(
                _settings_body(sender_app_password="new-app-password"),
                db=self.db,
            )

        row = self.db.query(IoclBalanceSettings).one()
        self.assertEqual(row.sender_email, "alerts@rdc.in")
        self.assertEqual(row.sender_app_password_encrypted, "new-cipher")
        self.assertIsNone(row.sender_user_id)
        self.assertTrue(result["mail_configured"])

    def test_saving_other_settings_keeps_the_dedicated_sender(self):
        result = put_settings(_settings_body(check_interval_minutes=45), db=self.db)

        self.assertEqual(result["sender_email"], "alerts@rdc.in")
        self.assertTrue(result["sender_app_password_configured"])
        self.assertEqual(self.db.query(IoclBalanceSettings).one().check_interval_minutes, 45)

    def test_changing_sender_email_without_password_clears_old_password(self):
        result = put_settings(_settings_body(sender_email="new-alerts@rdc.in"), db=self.db)

        self.assertEqual(result["sender_email"], "new-alerts@rdc.in")
        self.assertFalse(result["sender_app_password_configured"])
        self.assertIsNone(self.db.query(IoclBalanceSettings).one().sender_app_password_encrypted)

    def test_email_automation_cannot_be_enabled_without_dedicated_sender(self):
        row = self.db.query(IoclBalanceSettings).one()
        row.sender_email = None
        row.sender_app_password_encrypted = None
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            put_settings(_settings_body(enabled=True, sender_email=None), db=self.db)
        self.assertEqual(ctx.exception.status_code, 400)


class SendTestMailTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        User.__table__.create(self.engine)
        IoclBalanceSettings.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.db.add(_settings(last_balance=Decimal("1245620.60")))
        self.db.add(User(
            id=1,
            email="admin@rdc.in",
            first_name="Admin",
            password_hash="x",
            role="admin",
            is_active=True,
            is_deleted=False,
        ))
        self.db.commit()
        self.user = self.db.query(User).filter(User.id == 1).one()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_test_mail_uses_dedicated_sender_and_only_admin_inbox(self):
        body = TestMailBody(
            mail_type="daily",
            subject_template="Balance on {date}",
            body_template="It is {balance}",
        )
        with patch("app.routers.iocl_balance.security.decrypt", return_value="app-pass"), \
             patch("app.routers.iocl_balance.send_mail") as mock_send:
            result = send_test_mail(body, db=self.db, current_user=self.user)

        self.assertEqual(result, {"ok": True, "sent_to": "admin@rdc.in"})
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["from_email"], "alerts@rdc.in")
        self.assertEqual(kwargs["app_password"], "app-pass")
        self.assertEqual(kwargs["to_addresses"], ["admin@rdc.in"])
        self.assertEqual(kwargs["cc_addresses"], [])
        self.assertIn("12,45,620.60", kwargs["html_body"])

    def test_requires_dedicated_sender(self):
        row = self.db.query(IoclBalanceSettings).one()
        row.sender_app_password_encrypted = None
        self.db.commit()
        body = TestMailBody(
            mail_type="daily",
            subject_template="Balance on {date}",
            body_template="It is {balance}",
        )

        with self.assertRaises(HTTPException) as ctx:
            send_test_mail(body, db=self.db, current_user=self.user)
        self.assertEqual(ctx.exception.status_code, 400)


class DedicatedSenderDeliveryTests(unittest.TestCase):
    def test_sends_from_dedicated_encrypted_sender(self):
        with patch("app.services.iocl_balance.monitor.security.decrypt", return_value="app-pass"), \
             patch("app.services.mailer_shared.send_mail") as mock_send:
            ok, message = _send_from_configured_sender(
                "alerts@rdc.in",
                "ciphertext",
                ["ops@rdc.in"],
                [],
                "IOCL Balance as on 27-Aug-2026",
                "Balance is 12,45,620.60",
            )

        self.assertTrue(ok)
        self.assertIn("alerts@rdc.in", message)
        self.assertEqual(mock_send.call_args.kwargs["from_email"], "alerts@rdc.in")

    def test_missing_sender_fails_without_falling_back(self):
        ok, message = _send_from_configured_sender(
            None,
            None,
            ["ops@rdc.in"],
            [],
            "subject",
            "body",
        )

        self.assertFalse(ok)
        self.assertIn("not configured", message)

    def test_smtp_failure_is_recorded_as_soft_failure(self):
        with patch("app.services.iocl_balance.monitor.security.decrypt", return_value="app-pass"), \
             patch("app.services.mailer_shared.send_mail", side_effect=RuntimeError("SMTP auth failed")):
            ok, message = _send_from_configured_sender(
                "alerts@rdc.in",
                "ciphertext",
                ["ops@rdc.in"],
                [],
                "subject",
                "body",
            )

        self.assertFalse(ok)
        self.assertIn("SMTP auth failed", message)


if __name__ == "__main__":
    unittest.main()
