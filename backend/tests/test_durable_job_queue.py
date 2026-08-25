import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

from openpyxl import Workbook

from app import config, database, jobs
from app.models import BackgroundJob
from app.routers.erp_converter import _job_convert


class DurableJobQueueIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def test_two_processes_execute_owner_isolated_jobs_from_one_queue(self):
        marker = uuid.uuid4().hex
        owner_a = 810_001
        owner_b = 810_002
        submitted = []
        output_paths = []
        for index in range(6):
            input_path = config.SCRATCH_DIR / f"queue-test-{marker}-{index}.xlsx"
            output_path = config.SCRATCH_DIR / f"queue-test-{marker}-{index}-out.xlsx"
            workbook = Workbook()
            workbook.active["A1"] = f"worker payload {index}"
            workbook.save(input_path)
            owner = owner_a if index % 2 == 0 else owner_b
            job_id = jobs.submit_job(
                _job_convert,
                str(input_path),
                str(output_path),
                input_path.name,
                owner_id=owner,
            )
            submitted.append((job_id, owner))
            output_paths.append(output_path)

        db = database.SessionLocal()
        try:
            db.query(BackgroundJob).filter(
                BackgroundJob.id.in_([job_id for job_id, _owner in submitted])
            ).update({BackgroundJob.priority: -100}, synchronize_session=False)
            db.commit()
        finally:
            db.close()

        env = os.environ.copy()
        env["APP_PROCESS_ROLE"] = "worker"
        probe_args = [
            sys.executable,
            "-m",
            "tests.worker_probe",
        ]
        for job_id, _owner in submitted:
            probe_args.extend(["--job-id", job_id])
        children = [
            subprocess.Popen(
                probe_args,
                cwd=config.BASE_DIR,
                env=env,
            )
            for _ in range(2)
        ]
        try:
            for child in children:
                self.assertEqual(child.wait(timeout=60), 0)

            for job_id, owner in submitted:
                job = jobs.get_job(job_id, owner_id=owner)
                self.assertIsNotNone(job)
                self.assertEqual(job["status"], "done")
                self.assertIsNone(jobs.get_job(job_id, owner_id=owner_b if owner == owner_a else owner_a))
                self.assertTrue(Path(job["result"]["output_path"]).is_file())
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for path in output_paths:
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
