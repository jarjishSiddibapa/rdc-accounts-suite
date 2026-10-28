"""Who sends an automatic report mail: the system email, or a chosen person's own email."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import security
from app.database import Base
from app.models import EmailSettings, ScheduledReportMail, User
from app.services import scheduled_report_mail as mail

KEY = "debtors-reports-auto-mailer"


class SenderTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[
            User.__table__, EmailSettings.__table__, ScheduledReportMail.__table__])
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)
        self.ready = self._person("ready@rdc.in", "Asha", "Rao", "asha.rao@gmail.com", "app-pass")
        self.no_mail = self._person("nomail@rdc.in", "Ben", "Roy", None, None)
        self.inactive = self._person("gone@rdc.in", "Cy", "Dev", "cy@gmail.com", "pw", active=False)

    def _person(self, email, first, last, sender, password, active=True):
        user = User(email=email, first_name=first, last_name=last, password_hash="x", role="user",
                    is_active=active, is_deleted=False)
        self.db.add(user)
        self.db.commit()
        self.db.add(EmailSettings(
            user_id=user.id, sender_email=sender,
            app_password_encrypted=security.encrypt(password) if password else None, is_deleted=False))
        self.db.commit()
        return user

    def test_only_active_people_with_their_own_email_can_be_chosen(self):
        options = mail.sender_options(self.db)
        self.assertEqual([(o["user_id"], o["name"], o["email"]) for o in options],
                         [(self.ready.id, "Asha Rao", "asha.rao@gmail.com")])

    def test_the_chosen_person_sends_and_nobody_chosen_means_the_system_email(self):
        person = mail.resolve_sender(self.db, self.ready.id)
        self.assertTrue(person["configured"])
        self.assertEqual((person["email"], person["app_password"]), ("asha.rao@gmail.com", "app-pass"))
        with patch.object(mail, "get_system_email_settings", lambda db: {
                "sender_email": "system@rdc.in", "app_password": "sys", "configured": True}):
            system = mail.resolve_sender(self.db, None)
        self.assertEqual(system["email"], "system@rdc.in")

    def test_a_person_who_lost_their_email_or_is_inactive_cannot_send(self):
        for user in (self.no_mail, self.inactive):
            sender = mail.resolve_sender(self.db, user.id)
            self.assertFalse(sender["configured"])
            self.assertIn("no longer", sender["problem"])

    def test_saving_a_sender_is_checked_and_kept(self):
        data = {"enabled": False, "days": [0], "time": "08:30", "to": [], "cc": [],
                "subject_template": "S {date}", "intro_template": "Hi {date}"}
        with patch.object(mail, "set_report_recipient_defaults"), \
                patch.object(mail, "get_report_recipient_defaults", lambda db, key: {"to": [], "cc": []}), \
                patch.object(mail, "get_system_email_settings", lambda db: {
                    "sender_email": None, "app_password": None, "configured": False}), \
                patch.object(mail.audit_middleware, "log_event"):
            with self.assertRaises(ValueError):
                mail.save_settings(self.db, KEY, {**data, "sender_user_id": self.no_mail.id})
            view = mail.save_settings(self.db, KEY, {**data, "sender_user_id": self.ready.id})
            self.assertEqual(view["sender_user_id"], self.ready.id)
            self.assertEqual(view["sender_email"], "asha.rao@gmail.com")
            self.assertTrue(view["sender_configured"])
            back = mail.save_settings(self.db, KEY, {**data, "sender_user_id": None})
            self.assertIsNone(back["sender_user_id"])
            self.assertFalse(back["sender_configured"])


if __name__ == "__main__":
    unittest.main()
