"""Every report-mail preview runs as a background job, and a job can only be queued
when its function is on the server's allow-list - otherwise "Preview email" fails."""

import unittest

from app import jobs
from app.services.gst_invoice_adder import auto_mail as gst_mail
from app.services.ultrafine_cashflow import auto_mail as cashflow_mail
from app.services.untagged_invoices import auto_mail as untagged_mail


class ReportMailJobsAreRegisteredTests(unittest.TestCase):
    def test_each_preview_job_can_be_queued(self):
        for module in (untagged_mail, gst_mail, cashflow_mail):
            with self.subTest(module=module.__name__):
                name = jobs._task_name(module.run_preview_job)
                self.assertEqual(jobs._resolve_task(name), module.run_preview_job)


if __name__ == "__main__":
    unittest.main()
