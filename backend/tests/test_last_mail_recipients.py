"""A report mail starts from the To / Cc of the last mail that was sent."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Application, ApplicationEmailRecipient, LastMailRecipients
from app.services import mailer_shared

APP = "unaccounted"


class LastMailRecipientsTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(
            engine, tables=[Application.__table__, ApplicationEmailRecipient.__table__, LastMailRecipients.__table__],
        )
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.db = self.factory()
        self.addCleanup(self.db.close)
        patcher = patch.object(mailer_shared, "SessionLocal", self.factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db.add(Application(key=APP, label="Unaccounted", company="RDC"))
        self.db.commit()
        mailer_shared.set_report_recipient_defaults(self.db, APP, ["admin@rdc.in"], ["boss@rdc.in"])
        self.db.commit()

    def test_without_a_sent_mail_the_admin_defaults_are_used(self):
        self.assertEqual(mailer_shared.get_prefill_recipients(self.db, APP),
                         {"to": ["admin@rdc.in"], "cc": ["boss@rdc.in"]})

    def test_the_last_sent_to_and_cc_come_next_and_the_admin_defaults_stay_untouched(self):
        mailer_shared.remember_recipients(APP, ["one@rdc.in", "two@rdc.in"], ["cc@rdc.in"])
        self.assertEqual(mailer_shared.get_prefill_recipients(self.db, APP),
                         {"to": ["one@rdc.in", "two@rdc.in"], "cc": ["cc@rdc.in"]})
        mailer_shared.remember_recipients(APP, ["three@rdc.in"], [])     # a later send replaces it
        self.assertEqual(mailer_shared.get_prefill_recipients(self.db, APP),
                         {"to": ["three@rdc.in"], "cc": []})
        self.assertEqual(mailer_shared.get_report_recipient_defaults(self.db, APP),
                         {"to": ["admin@rdc.in"], "cc": ["boss@rdc.in"]})

    def test_each_application_keeps_its_own(self):
        mailer_shared.remember_recipients("ultrafine-cashflow", ["cf@rdc.in"], [])
        self.assertEqual(mailer_shared.get_prefill_recipients(self.db, APP)["to"], ["admin@rdc.in"])
        self.assertEqual(mailer_shared.get_prefill_recipients(self.db, "ultrafine-cashflow")["to"], ["cf@rdc.in"])

    def test_nothing_is_remembered_without_a_to_recipient(self):
        mailer_shared.remember_recipients(APP, [], ["cc@rdc.in"])
        self.assertEqual(mailer_shared.get_prefill_recipients(self.db, APP)["to"], ["admin@rdc.in"])


if __name__ == "__main__":
    unittest.main()
