import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth
from app.database import Base
from app.models import EmailSettings, User
from app.routers import settings_routes
from app.routers.settings_routes import ChangePasswordBody, EmailSettingsBody

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


class RevealAppPasswordTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[User.__table__, EmailSettings.__table__])
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()
        self.user = User(email="a@rdc.in", password_hash=auth.hash_password(OLD), role="user")
        self.db.add(self.user)
        self.db.commit()
        patcher = patch.object(settings_routes, "auth_limiter", SimpleNamespace(enforce=lambda *a, **k: None))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_own_saved_app_password(self):
        settings_routes.put_email_settings(
            EmailSettingsBody(sender_email="a@rdc.in", app_password="abcd efgh ijkl mnop"), self.db, self.user,
        )
        result = settings_routes.reveal_email_app_password(self.db, self.user)
        self.assertEqual(result, {"app_password": "abcd efgh ijkl mnop"})

    def test_404_when_nothing_saved(self):
        with self.assertRaises(HTTPException) as caught:
            settings_routes.reveal_email_app_password(self.db, self.user)
        self.assertEqual(caught.exception.status_code, 404)


class RevealSecretHelperTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("app.secret_reveal.auth_limiter", SimpleNamespace(enforce=lambda *a, **k: None))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_round_trips_an_encrypted_value(self):
        from app import security
        from app.secret_reveal import reveal_secret
        self.assertEqual(reveal_secret(security.encrypt("s3cret"), user_id=1, scope="t"), {"value": "s3cret"})

    def test_missing_value_is_404_and_garbage_is_500(self):
        from app.secret_reveal import reveal_secret
        with self.assertRaises(HTTPException) as missing:
            reveal_secret(None, user_id=1, scope="t")
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as bad:
            reveal_secret("not-a-valid-token", user_id=1, scope="t")
        self.assertEqual(bad.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
