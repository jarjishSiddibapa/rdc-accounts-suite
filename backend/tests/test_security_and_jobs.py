import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import Response

from app import auth, jobs as jobs_module
from app.jobs import (
    cancel_job,
    claim_job_action,
    finish_job_action,
    get_job,
    submit_inline_job,
)
from app.oracle_runtime import initialize_oracle_client
from app.routers import unaccounted_txn
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
        password = "A-secure-password1!"
        hashed = auth.hash_password(password)
        self.assertTrue(auth.verify_password(password, hashed))
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
        key = f"login:test:{uuid.uuid4()}"
        limiter.enforce(key, limit=2, window_seconds=60)
        limiter.enforce(key, limit=2, window_seconds=60)
        with self.assertRaises(HTTPException) as raised:
            limiter.enforce(key, limit=2, window_seconds=60)
        self.assertEqual(raised.exception.status_code, 429)

    def test_shared_limiter_is_atomic_under_parallel_requests(self):
        limiter = SlidingWindowLimiter()
        key = f"login:parallel:{uuid.uuid4()}"

        def attempt(_index):
            try:
                limiter.enforce(key, limit=5, window_seconds=60)
                return "allowed"
            except HTTPException as exc:
                self.assertEqual(exc.status_code, 429)
                return "blocked"

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(attempt, range(20)))
        self.assertEqual(outcomes.count("allowed"), 5)
        self.assertEqual(outcomes.count("blocked"), 15)


class OracleRuntimeTests(unittest.TestCase):
    def test_oracle_client_initialization_is_process_wide_and_thread_safe(self):
        class FakeOracleDriver:
            def __init__(self):
                self.calls = 0

            def init_oracle_client(self, *, lib_dir):
                self.calls += 1
                time.sleep(0.01)

        driver = FakeOracleDriver()
        with patch("app.oracle_runtime._oracle_init_attempted", False):
            with ThreadPoolExecutor(max_workers=16) as pool:
                list(
                    pool.map(
                        lambda _: initialize_oracle_client(driver, "C:/oracle/client"),
                        range(32),
                    )
                )

        self.assertEqual(driver.calls, 1)


class JobIsolationTests(unittest.TestCase):
    @staticmethod
    def _completed_job(owner_id: int) -> str:
        job_id = submit_inline_job(lambda: {"status": "preview"}, owner_id=owner_id)
        deadline = time.monotonic() + 2
        job = get_job(job_id, owner_id=owner_id)
        while job and job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            job = get_job(job_id, owner_id=owner_id)
        if not job or job["status"] != "done":
            raise AssertionError("Test job did not finish")
        return job_id

    def test_jobs_are_visible_only_to_their_owner(self):
        job_id = submit_inline_job(lambda: {"ok": True}, owner_id=101)
        self.assertIsNone(get_job(job_id, owner_id=202))

        deadline = time.monotonic() + 2
        owned_job = get_job(job_id, owner_id=101)
        while owned_job and owned_job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            owned_job = get_job(job_id, owner_id=101)

        self.assertIsNotNone(owned_job)
        self.assertEqual(owned_job["status"], "done")
        self.assertEqual(owned_job["result"], {"ok": True})

    def test_parallel_jobs_for_multiple_users_remain_owner_isolated(self):
        owners = [901 + (index % 6) for index in range(30)]
        with ThreadPoolExecutor(max_workers=20) as pool:
            job_ids = list(
                pool.map(
                    lambda owner: submit_inline_job(
                        lambda value=owner: {"owner_marker": value},
                        owner_id=owner,
                    ),
                    owners,
                )
            )

        deadline = time.monotonic() + 3
        for job_id, owner in zip(job_ids, owners):
            job = get_job(job_id, owner_id=owner)
            while job and job["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.005)
                job = get_job(job_id, owner_id=owner)
            self.assertEqual(job["status"], "done")
            self.assertEqual(job["result"], {"owner_marker": owner})
            other_owner = 999 if owner != 999 else 998
            self.assertIsNone(get_job(job_id, owner_id=other_owner))

    def test_running_job_can_be_cancelled_only_by_its_owner(self):
        started = Event()
        continue_work = Event()

        def cancellable_work(progress_cb=None):
            started.set()
            continue_work.wait(timeout=2)
            progress_cb(0.5, "Halfway")
            return {"should_not": "complete"}

        job_id = submit_inline_job(cancellable_work, owner_id=303)
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

    def test_one_shot_action_can_be_claimed_by_only_one_tab(self):
        job_id = self._completed_job(owner_id=505)

        with ThreadPoolExecutor(max_workers=12) as pool:
            states = list(
                pool.map(
                    lambda _: claim_job_action(
                        job_id,
                        owner_id=505,
                        action="confirm-send",
                    )[0],
                    range(12),
                )
            )

        self.assertEqual(states.count("claimed"), 1)
        self.assertEqual(states.count("in_progress"), 11)
        self.assertTrue(
            finish_job_action(
                job_id,
                owner_id=505,
                action="confirm-send",
                succeeded=True,
            )
        )
        state, _ = claim_job_action(job_id, owner_id=505, action="confirm-send")
        self.assertEqual(state, "completed")

        public_job = get_job(job_id, owner_id=505)
        self.assertNotIn("_actions", public_job)

    def test_one_shot_action_claim_respects_owner_and_preserves_failed_state(self):
        job_id = self._completed_job(owner_id=606)

        state, job = claim_job_action(job_id, owner_id=707, action="confirm-send")
        self.assertEqual(state, "missing")
        self.assertIsNone(job)

        state, _ = claim_job_action(job_id, owner_id=606, action="confirm-send")
        self.assertEqual(state, "claimed")
        self.assertTrue(
            finish_job_action(
                job_id,
                owner_id=606,
                action="confirm-send",
                succeeded=False,
            )
        )
        state, _ = claim_job_action(job_id, owner_id=606, action="confirm-send")
        self.assertEqual(state, "failed")

    def test_cpu_executor_is_created_once_under_concurrent_startup(self):
        created = []

        class FakeExecutor:
            def __init__(self, *, max_workers):
                self.max_workers = max_workers
                created.append(self)

            def shutdown(self, **_kwargs):
                pass

        with (
            patch("app.jobs._cpu_executor", None),
            patch("app.jobs.ProcessPoolExecutor", FakeExecutor),
            patch("app.jobs.atexit.register"),
        ):
            with ThreadPoolExecutor(max_workers=16) as pool:
                executors = list(pool.map(lambda _: jobs_module._get_cpu_executor(), range(32)))

        self.assertEqual(len(created), 1)
        self.assertTrue(all(executor is created[0] for executor in executors))

    def test_two_tabs_cannot_confirm_the_same_email_preview_twice(self):
        owner_id = 808
        preview_job_id = submit_inline_job(
            lambda: {
                "status": "preview",
                "to": ["recipient@example.com"],
                "cc": [],
                "subject": "Concurrent preview",
                "html_body": "<p>Test</p>",
                "attachments": [],
            },
            owner_id=owner_id,
        )
        deadline = time.monotonic() + 2
        preview_job = get_job(preview_job_id, owner_id=owner_id)
        while (
            preview_job
            and preview_job["status"] == "running"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            preview_job = get_job(preview_job_id, owner_id=owner_id)

        first_send_started = Event()
        allow_first_send_to_finish = Event()
        send_calls = []

        def fake_send_mail(**kwargs):
            send_calls.append(kwargs)
            first_send_started.set()
            allow_first_send_to_finish.wait(timeout=2)

        body = unaccounted_txn.ConfirmSendBody(job_id=preview_job_id)
        user = SimpleNamespace(id=owner_id)
        settings = {
            "configured": True,
            "email": "sender@example.com",
            "app_password": "secret",
        }

        with (
            patch.object(unaccounted_txn.mailer_shared, "get_email_settings", return_value=settings),
            patch.object(unaccounted_txn.mailer_shared, "send_mail", side_effect=fake_send_mail),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(unaccounted_txn.mail_confirm_send, body, user)
            self.assertTrue(first_send_started.wait(timeout=2))
            second = pool.submit(unaccounted_txn.mail_confirm_send, body, user)
            with self.assertRaises(HTTPException) as raised:
                second.result(timeout=2)
            self.assertEqual(raised.exception.status_code, 409)
            allow_first_send_to_finish.set()
            self.assertEqual(first.result(timeout=2)["status"], "sent")

        self.assertEqual(len(send_calls), 1)


if __name__ == "__main__":
    unittest.main()
