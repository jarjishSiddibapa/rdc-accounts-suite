import time
import unittest
from threading import Event
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import Response

from app import auth
from app.jobs import cancel_job, get_job, submit_job
from app.rate_limit import SlidingWindowLimiter
from app.validation import normalize_email, normalize_optional_name, validate_password


class ValidationTests(unittest.TestCase):
    def test_email_is_normalized(self):
        self.assertEqual(normalize_email("  Person@Example.COM "), "person@example.com")

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_email("not-an-email")

    def test_optional_names_are_normalized(self):
        self.assertEqual(normalize_optional_name("  Sneha   Raman  "), "Sneha Raman")
        self.assertIsNone(normalize_optional_name("   "))
        self.assertIsNone(normalize_optional_name(None))

    def test_name_length_and_control_characters_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_optional_name("a" * 101)
        with self.assertRaises(ValueError):
            normalize_optional_name("Sneha\x00Raman")

    def test_password_bounds_are_enforced(self):
        with self.assertRaises(ValueError):
            validate_password("too-short")
        with self.assertRaises(ValueError):
            validate_password("é" * 37)  # 74 UTF-8 bytes, beyond bcrypt's safe limit

    def test_password_hash_round_trip(self):
        hashed = auth.hash_password("a-secure-password")
        self.assertTrue(auth.verify_password("a-secure-password", hashed))
        self.assertFalse(auth.verify_password("not-the-password", hashed))

    def test_login_cookie_is_browser_session_only(self):
        response = Response()
        user = SimpleNamespace(id=1, email="person@example.com", password_hash="hash")
        auth.create_session(response, user)
        set_cookie = response.headers["set-cookie"].lower()
        self.assertNotIn("max-age=", set_cookie)
        self.assertNotIn("expires=", set_cookie)

    def test_session_expires_after_twenty_minutes_without_activity(self):
        response = Response()
        user = SimpleNamespace(id=1, email="person@example.com", password_hash="hash")
        issued_at = 1_800_000_000
        with patch("app.auth.time.time", return_value=issued_at):
            auth.create_session(response, user)

        cookie = SimpleCookie()
        cookie.load(response.headers["set-cookie"])
        token = cookie["session"].value
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/auth/me",
                "headers": [(b"cookie", f"session={token}".encode("latin-1"))],
            }
        )

        with patch(
            "app.auth.time.time",
            return_value=issued_at + auth.SESSION_IDLE_TIMEOUT - 1,
        ):
            self.assertIsNotNone(auth._load_session_data(request))
        with patch(
            "app.auth.time.time",
            return_value=issued_at + auth.SESSION_IDLE_TIMEOUT,
        ):
            self.assertIsNone(auth._load_session_data(request))


class RateLimitTests(unittest.TestCase):
    def test_limiter_blocks_after_limit(self):
        limiter = SlidingWindowLimiter()
        limiter.enforce("login:client", limit=2, window_seconds=60)
        limiter.enforce("login:client", limit=2, window_seconds=60)
        with self.assertRaises(HTTPException) as raised:
            limiter.enforce("login:client", limit=2, window_seconds=60)
        self.assertEqual(raised.exception.status_code, 429)


class JobIsolationTests(unittest.TestCase):
    def test_jobs_are_visible_only_to_their_owner(self):
        job_id = submit_job(lambda: {"ok": True}, owner_id=101)
        self.assertIsNone(get_job(job_id, owner_id=202))

        deadline = time.monotonic() + 2
        owned_job = get_job(job_id, owner_id=101)
        while owned_job and owned_job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            owned_job = get_job(job_id, owner_id=101)

        self.assertIsNotNone(owned_job)
        self.assertEqual(owned_job["status"], "done")
        self.assertEqual(owned_job["result"], {"ok": True})

    def test_running_job_can_be_cancelled_only_by_its_owner(self):
        started = Event()
        continue_work = Event()

        def cancellable_work(progress_cb=None):
            started.set()
            continue_work.wait(timeout=2)
            progress_cb(0.5, "Halfway")
            return {"should_not": "complete"}

        job_id = submit_job(cancellable_work, owner_id=303)
        self.assertTrue(started.wait(timeout=2))
        self.assertIsNone(cancel_job(job_id, owner_id=404))
        cancellation = cancel_job(job_id, owner_id=303)
        self.assertIsNotNone(cancellation)
        self.assertEqual(cancellation["phase"], "Cancelling...")
        continue_work.set()

        deadline = time.monotonic() + 2
        owned_job = get_job(job_id, owner_id=303)
        while owned_job and owned_job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            owned_job = get_job(job_id, owner_id=303)

        self.assertIsNotNone(owned_job)
        self.assertEqual(owned_job["status"], "cancelled")
        self.assertIsNone(owned_job["result"])


if __name__ == "__main__":
    unittest.main()
