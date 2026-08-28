"""Regression coverage for the other half of the empty-attachment production
incident: the scratch-cleanup sweep was deleting unaccounted_txn's pending
mail-preview directory (SCRATCH_DIR / "mail-<uuid>") using the same short,
admin-configured scratch_cleanup_minutes as any other one-shot scratch file,
even though nothing bounds how long a human takes to review a preview before
clicking "confirm send". Every tool with this same "preview, then a separate
confirm-send click" shape - unaccounted_txn ("mail-*"), Ultrafine Balance
Confirmation ("balance-confirm-*"), and Ultrafine Payment Reminder
("payment-reminder-*") - must get a much longer, dedicated grace period,
independent of that general setting."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import scheduler


class ScratchSweepMailExemptionTests(unittest.TestCase):
    def setUp(self):
        scheduler._last_scratch_sweep = 0.0  # force the throttle to allow a sweep

    def _run_sweep_with(self, scratch_dir, scratch_cleanup_minutes: int):
        scheduler._last_scratch_sweep = 0.0  # each call is a fresh sweep, not throttled by a prior one
        fake_settings = SimpleNamespace(scratch_cleanup_minutes=scratch_cleanup_minutes)
        with (
            patch.object(scheduler, "SCRATCH_DIR", scratch_dir),
            patch.object(scheduler, "_get_or_create_settings", return_value=fake_settings),
            patch.object(scheduler, "SessionLocal", return_value=SimpleNamespace(close=lambda: None)),
            patch.object(scheduler.audit_middleware, "log_event"),
        ):
            scheduler._sweep_scratch()

    def _age(self, path, minutes_old: float) -> None:
        stamp = time.time() - minutes_old * 60
        os.utime(path, (stamp, stamp))

    def test_a_60_minute_old_pending_send_directory_survives_a_30_minute_setting(self):
        for prefix in ("mail-", "balance-confirm-", "payment-reminder-"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as tmp:
                scratch_dir = Path(tmp)
                pending_dir = scratch_dir / f"{prefix}11111111-1111-1111-1111-111111111111"
                pending_dir.mkdir()
                (pending_dir / "Report.xlsx").write_bytes(b"fake xlsx content")
                self._age(pending_dir, minutes_old=60)

                self._run_sweep_with(scratch_dir, scratch_cleanup_minutes=30)

                self.assertTrue(pending_dir.is_dir(), f"a 60-minute-old {prefix} preview must not be reaped at 30 minutes")
                self.assertTrue((pending_dir / "Report.xlsx").is_file())

    def test_a_25_hour_old_pending_send_directory_is_finally_reaped(self):
        for prefix in ("mail-", "balance-confirm-", "payment-reminder-"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as tmp:
                scratch_dir = Path(tmp)
                pending_dir = scratch_dir / f"{prefix}22222222-2222-2222-2222-222222222222"
                pending_dir.mkdir()
                (pending_dir / "Report.xlsx").write_bytes(b"fake xlsx content")
                self._age(pending_dir, minutes_old=25 * 60)

                self._run_sweep_with(scratch_dir, scratch_cleanup_minutes=30)

                self.assertFalse(pending_dir.exists(), f"an abandoned {prefix} preview must still eventually be reclaimed")

    def test_a_plain_scratch_file_still_honors_the_short_admin_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch_dir = Path(tmp)
            stray_upload = scratch_dir / "some-upload.xlsx"
            stray_upload.write_bytes(b"leftover upload")
            self._age(stray_upload, minutes_old=60)

            self._run_sweep_with(scratch_dir, scratch_cleanup_minutes=30)

            self.assertFalse(stray_upload.exists(), "ordinary scratch files must still respect scratch_cleanup_minutes")


if __name__ == "__main__":
    unittest.main()
