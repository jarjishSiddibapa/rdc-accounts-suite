"""Login timing for unknown emails, and where a password-reset link points."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app import auth
from app.routers import auth_routes


def _request(host: str | None, base_url: str = "http://192.168.0.84:2805/"):
    headers = {} if host is None else {"host": host}
    return SimpleNamespace(headers=headers, base_url=base_url, client=SimpleNamespace(host="10.0.0.9"))


class ResetLinkBaseTests(unittest.TestCase):
    def setUp(self):
        auth_routes._own_host_names.cache_clear()
        patcher = patch.object(auth_routes, "_own_host_names", return_value=frozenset({"ho-ipo-0b20z", "192.168.0.84", "localhost"}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_configured_address_is_always_used(self):
        with patch.object(auth_routes.config, "APP_BASE_URL", "http://accounts.rdc.in"):
            self.assertEqual(auth_routes._reset_link_base(_request("evil.example")), "http://accounts.rdc.in")

    def test_this_servers_own_name_or_address_is_trusted(self):
        with patch.object(auth_routes.config, "APP_BASE_URL", ""):
            for host in ("192.168.0.84:2805", "HO-IPO-0B20Z:2805", "localhost:2805", "192.168.0.84"):
                self.assertEqual(auth_routes._reset_link_base(_request(host)), "http://192.168.0.84:2805", host)

    def test_a_host_the_sender_chose_is_refused(self):
        with patch.object(auth_routes.config, "APP_BASE_URL", ""):
            for host in ("evil.example", "192.168.0.99:2805", "192.168.0.84.evil.example", "[bad", "", None):
                self.assertIsNone(auth_routes._reset_link_base(_request(host)), host)


class LoginTimingTests(unittest.TestCase):
    def test_an_unknown_email_still_pays_for_a_password_check(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        body = SimpleNamespace(email="nobody@rdc.in", password="whatever1")
        with patch.object(auth_routes.auth_limiter, "enforce"), \
                patch.object(auth_routes.auth, "spend_password_check_time") as spent:
            with self.assertRaises(HTTPException) as caught:
                auth_routes.login(body, _request("192.168.0.84:2805"), MagicMock(), db)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "Invalid email or password")
        spent.assert_called_once_with("whatever1")

    def test_the_stand_in_check_takes_about_as_long_as_a_real_one(self):
        real = auth.hash_password("Correct-horse-9")
        started = time.perf_counter()
        auth.verify_password("wrong-password", real)
        real_time = time.perf_counter() - started
        started = time.perf_counter()
        auth.spend_password_check_time("wrong-password")
        stand_in = time.perf_counter() - started
        self.assertGreater(stand_in, real_time * 0.5)
        self.assertLess(stand_in, real_time * 2)


if __name__ == "__main__":
    unittest.main()
