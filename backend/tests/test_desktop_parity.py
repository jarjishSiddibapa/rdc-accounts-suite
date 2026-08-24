import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from app.routers import rdc_payables, unaccounted_txn
from app.services.dms import fetcher


class DesktopParityTests(unittest.TestCase):
    def test_dms_permalink_without_scheme_receives_desktop_https_prefix(self):
        self.assertEqual(
            fetcher._validate_url("dms.example.com/document/123"),
            "https://dms.example.com/document/123",
        )

    def test_payables_job_returns_desktop_stats_breakdowns_and_log(self):
        frame = pd.DataFrame(
            {
                "Region": ["West", "", "North"],
                "Vendor Site Code": ["A", "B", "C"],
                "Transaction Type": ["Invoice", "Invoice", "TDS"],
                "Aging Bucket": ["0-30", "31-60", "0-30"],
            }
        )
        fake_session = SimpleNamespace(close=lambda: None)
        progress = []

        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.xls"
            output_path = Path(temp_dir) / "output.xlsx"
            input_path.write_text("placeholder", encoding="utf-8")

            with (
                patch.object(rdc_payables, "SessionLocal", return_value=fake_session),
                patch.object(
                    rdc_payables.mapping_store,
                    "load_all",
                    return_value=({}, {}, set(), {}, {}, {}),
                ),
                patch.object(
                    rdc_payables.processor,
                    "parse_html_report",
                    return_value=(["Column"], [[1], [2], [3], [4]]),
                ),
                patch.object(
                    rdc_payables.processor,
                    "process_report",
                    return_value=(frame, datetime(2026, 7, 31)),
                ),
                patch.object(rdc_payables.processor, "to_excel_bytes", return_value=b"xlsx"),
            ):
                result = rdc_payables._run_process_job(
                    str(input_path),
                    str(output_path),
                    2026,
                    7,
                    progress_cb=lambda fraction, phase: progress.append((fraction, phase)),
                )

        self.assertEqual(result["raw_row_count"], 4)
        self.assertEqual(result["row_count"], 3)
        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(result["unmatched_count"], 1)
        self.assertEqual(result["transaction_type_counts"], {"Invoice": 2, "TDS": 1})
        self.assertEqual(result["aging_bucket_counts"], {"0-30": 2, "31-60": 1})
        self.assertEqual(result["download_filename"], "Payables_Report_Through_Jul-2026.xlsx")
        self.assertTrue(any("Unmapped site codes: 1" in line for line in result["log"]))
        self.assertEqual(progress[-1], (1.0, "Report ready"))

    def test_mail_job_uses_desktop_attachment_filename_pattern(self):
        frame = pd.DataFrame({"Supplier Site": ["SITE-1"], "Location": ["West"]})

        def write_workbook(_frame, output_path):
            Path(output_path).write_bytes(b"xlsx")

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(unaccounted_txn, "SCRATCH_DIR", Path(temp_dir)),
                patch.object(
                    unaccounted_txn.processing,
                    "process_report_multi",
                    return_value=(frame, 1, 2, 1),
                ),
                patch.object(
                    unaccounted_txn.excel_writers,
                    "write_formatted_excel",
                    side_effect=write_workbook,
                ),
                patch.object(
                    unaccounted_txn.mailer_shared,
                    "get_email_settings",
                    return_value={"configured": True, "email": "sender@example.com", "signature": ""},
                ),
                patch.object(
                    unaccounted_txn.mailer_shared,
                    "build_email_content",
                    return_value=("Subject", "<p>Body</p>"),
                ),
            ):
                result = unaccounted_txn._job_mail_send(
                    1,
                    ["input.xls"],
                    None,
                    None,
                    set(),
                    set(),
                    ["land rent"],
                    0.82,
                    "Jul-26",
                    "Jul-26",
                    "Jul-26",
                    "Jul-26",
                    "21.08.2026",
                    True,
                    False,
                    False,
                    "Subject",
                    "Intro",
                    ["to@example.com"],
                    [],
                    False,
                )

        attachment = Path(result["attachments"][0])
        self.assertEqual(attachment.name, "Unaccounted Jul-26 as on 21.08.2026.xlsx")
        self.assertEqual(result["output_paths"]["unaccounted"], str(attachment))
        self.assertEqual(result["status"], "preview")


if __name__ == "__main__":
    unittest.main()
