import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routers import unaccounted_txn


class PeriodDetectionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_detection_endpoint_rejects_an_empty_result_and_removes_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload_path = Path(temp_dir) / "pending-mrn.xls"
            upload_path.write_text("placeholder", encoding="utf-8")

            with (
                patch.object(unaccounted_txn, "_save_upload", AsyncMock(return_value=upload_path)),
                patch.object(
                    unaccounted_txn.processing,
                    "detect_mrn_periods",
                    return_value=[],
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await unaccounted_txn.detect_mrn_periods(file=object(), user=object())

            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("cannot continue", raised.exception.detail)
            self.assertFalse(upload_path.exists())

    async def test_mrn_processing_never_queues_before_period_detection_succeeds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload_path = Path(temp_dir) / "pending-mrn.xls"
            upload_path.write_text("placeholder", encoding="utf-8")

            with (
                patch.object(unaccounted_txn, "_save_upload", AsyncMock(return_value=upload_path)),
                patch.object(
                    unaccounted_txn.processing,
                    "detect_mrn_periods",
                    return_value=[],
                ),
                patch.object(unaccounted_txn, "submit_job") as submit_job,
            ):
                with self.assertRaises(HTTPException) as raised:
                    await unaccounted_txn.process_mrn(
                        file=SimpleNamespace(filename="pending-mrn.xls"),
                        exclude_periods="",
                        user=SimpleNamespace(id=7),
                    )

            self.assertEqual(raised.exception.status_code, 422)
            submit_job.assert_not_called()
            self.assertFalse(upload_path.exists())

    async def test_processing_rejects_exclusions_from_a_stale_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload_path = Path(temp_dir) / "expense-po.xls"
            upload_path.write_text("placeholder", encoding="utf-8")

            with (
                patch.object(unaccounted_txn, "_save_upload", AsyncMock(return_value=upload_path)),
                patch.object(
                    unaccounted_txn.processing,
                    "detect_po_periods",
                    return_value=["Apr-26", "May-26"],
                ),
                patch.object(unaccounted_txn, "submit_job") as submit_job,
            ):
                with self.assertRaises(HTTPException) as raised:
                    await unaccounted_txn.process_po(
                        file=SimpleNamespace(filename="expense-po.xls"),
                        exclude_months="Jun-26",
                        keywords="",
                        fuzzy_threshold=None,
                        user=SimpleNamespace(id=7),
                        db=object(),
                    )

            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("do not match", raised.exception.detail)
            submit_job.assert_not_called()
            self.assertFalse(upload_path.exists())

    async def test_combined_mail_never_queues_when_mrn_detection_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload_path = Path(temp_dir) / "pending-mrn.xls"
            upload_path.write_text("placeholder", encoding="utf-8")

            with (
                patch.object(
                    unaccounted_txn.mailer_shared,
                    "get_email_settings",
                    return_value={"configured": True},
                ),
                patch.object(unaccounted_txn, "_save_upload", AsyncMock(return_value=upload_path)),
                patch.object(
                    unaccounted_txn.processing,
                    "detect_mrn_periods",
                    return_value=[],
                ),
                patch.object(unaccounted_txn, "submit_job") as submit_job,
            ):
                with self.assertRaises(HTTPException) as raised:
                    await unaccounted_txn.mail_send(
                        ua_files=[],
                        mrn_file=SimpleNamespace(filename="pending-mrn.xls"),
                        po_file=None,
                        exclude_periods="",
                        exclude_months="",
                        keywords="",
                        fuzzy_threshold=None,
                        month_subject="Aug-26",
                        month_ua="Aug-26",
                        month_mrn="Aug-26",
                        month_po="Aug-26",
                        as_on_date="26.08.2026",
                        include_ua=False,
                        include_mrn=True,
                        include_po=False,
                        custom_subject="Subject",
                        custom_intro="Body",
                        to="to@example.com",
                        cc="",
                        force_send=False,
                        user=SimpleNamespace(id=7),
                        db=object(),
                    )

            self.assertEqual(raised.exception.status_code, 422)
            submit_job.assert_not_called()
            self.assertFalse(upload_path.exists())


if __name__ == "__main__":
    unittest.main()
