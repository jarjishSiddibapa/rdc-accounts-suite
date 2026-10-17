import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth
from app.database import Base
from app.models import User
from app.routers import settings_routes
from app.routers.settings_routes import ChangePasswordBody

OLD = "Old-Passw0rd!x"
NEW = "New-Passw0rd!y"


class ChangePasswordTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[User.__table__])
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()
        self.user = User(email="a@rdc.in", password_hash=auth.hash_password(OLD), role="user")
        self.db.add(self.user)
        self.db.commit()
        # the real limiter talks to MySQL
        patcher = patch.object(settings_routes, "auth_limiter", SimpleNamespace(enforce=lambda *a, **k: None))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _change(self, current, new):
        response = Response()
        result = settings_routes.change_password(
            ChangePasswordBody(current_password=current, new_password=new), response, self.db, self.user,
        )
        return result, response

    def test_changes_password_and_keeps_this_browser_signed_in(self):
        old_marker = auth._credential_marker(self.user)
        result, response = self._change(OLD, NEW)
        self.assertEqual(result, {"ok": True})
        self.assertTrue(auth.verify_password(NEW, self.user.password_hash))
        self.assertFalse(auth.verify_password(OLD, self.user.password_hash))
        # a fresh session cookie is issued, tied to the new password
        cookie = response.headers.get("set-cookie", "")
        self.assertIn("session=", cookie)
        self.assertNotEqual(old_marker, auth._credential_marker(self.user))

    def test_wrong_current_password_is_refused_and_nothing_changes(self):
        with self.assertRaises(HTTPException) as caught:
            self._change("Not-The-Passw0rd!", NEW)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("current password is incorrect", caught.exception.detail)
        self.assertTrue(auth.verify_password(OLD, self.user.password_hash))

    def test_new_password_must_differ_from_current(self):
        with self.assertRaises(HTTPException) as caught:
            self._change(OLD, OLD)
        self.assertIn("different", caught.exception.detail)

    def test_weak_new_password_is_rejected_by_validation(self):
        with self.assertRaises(ValidationError):
            ChangePasswordBody(current_password=OLD, new_password="short")
        with self.assertRaises(ValidationError):
            ChangePasswordBody(current_password=OLD, new_password="alllowercase123")

    def test_empty_current_password_is_rejected_by_validation(self):
        with self.assertRaises(ValidationError):
            ChangePasswordBody(current_password="", new_password=NEW)


if __name__ == "__main__":
    unittest.main()
