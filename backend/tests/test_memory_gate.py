"""Memory: the server's real figures, the default worker count sized from them, and a job that waits to start
while the server is short of free memory."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app import config, jobs, memory
from app.database import Base
from app.models import BackgroundJob, BackgroundResourceSlot


@compiles(LONGTEXT, "sqlite")
def _longtext_on_sqlite(element, compiler, **kw):
    return "TEXT"


@compiles(BigInteger, "sqlite")
def _bigint_on_sqlite(element, compiler, **kw):
    return "INTEGER"


NOW = datetime(2026, 11, 7, 12, 0, 0)


class MemoryReadingTests(unittest.TestCase):
    def test_the_figures_are_real_and_consistent(self):
        total, available = memory.total_mb(), memory.available_mb()
        self.assertIsNotNone(total)
        self.assertIsNotNone(available)
        self.assertGreater(total, 512)
        self.assertGreater(available, 0)
        self.assertLessEqual(available, total)

    def test_an_unreadable_figure_is_none_never_an_error(self):
        with patch.object(memory, "_windows", side_effect=OSError("no api")), \
                patch.object(memory, "_linux", side_effect=OSError("no proc")):
            self.assertIsNone(memory.total_mb())
            self.assertIsNone(memory.available_mb())


class DefaultWorkersTests(unittest.TestCase):
    def _workers(self, free_mb, cpus):
        with patch.object(memory, "available_mb", return_value=free_mb), \
                patch.object(jobs.os, "cpu_count", return_value=cpus):
            return jobs._default_cpu_pool_workers()

    def test_a_server_with_plenty_of_free_memory_gets_more_than_the_8gb_default(self):
        self.assertEqual(self._workers(12_000, 8), 2)

    def test_little_free_memory_keeps_it_at_one(self):
        self.assertEqual(self._workers(1_500, 8), 1)
        self.assertEqual(self._workers(1_600, 8), 1)
        self.assertEqual(self._workers(1_900, 8), 2)          # room for a second helper once ~250 MB more is free

    def test_it_is_still_limited_by_the_cpus_as_before(self):
        self.assertEqual(self._workers(20_000, 2), 1)       # two cpus leave one for everything else
        self.assertEqual(self._workers(20_000, 4), 2)

    def test_an_unreadable_figure_falls_back_to_the_documented_8gb_host(self):
        self.assertEqual(self._workers(None, 8), 1)


class MemoryWaitTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("JOB_MIN_FREE_MEMORY_MB", 1024), ("JOB_MEMORY_WAIT_MAX_SECONDS", 600)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_it_waits_only_while_memory_is_short(self):
        recent = NOW - timedelta(seconds=30)
        self.assertTrue(jobs.memory_wait(recent, NOW, 500))
        self.assertFalse(jobs.memory_wait(recent, NOW, 1024))
        self.assertFalse(jobs.memory_wait(recent, NOW, 12_000))

    def test_it_never_waits_forever(self):
        self.assertTrue(jobs.memory_wait(NOW - timedelta(seconds=599), NOW, 100))
        self.assertFalse(jobs.memory_wait(NOW - timedelta(seconds=601), NOW, 100))

    def test_an_unreadable_figure_never_makes_a_job_wait(self):
        self.assertFalse(jobs.memory_wait(NOW, NOW, None))

    def test_the_check_can_be_switched_off(self):
        with patch.object(config, "JOB_MIN_FREE_MEMORY_MB", 0):
            self.assertFalse(jobs.memory_wait(NOW, NOW, 1))


class ClaimTests(unittest.TestCase):
    """The real claim, on an in-memory database: a queued job stays queued while memory is short."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[BackgroundJob.__table__, BackgroundResourceSlot.__table__])
        factory = sessionmaker(bind=engine)
        self.db = factory()
        self.addCleanup(self.db.close)
        for patcher in (
            patch.object(jobs, "SessionLocal", factory),
            patch.object(config, "JOB_MIN_FREE_MEMORY_MB", 1024),
            patch.object(config, "JOB_MEMORY_WAIT_MAX_SECONDS", 600),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.db.add(BackgroundJob(
            id="job-1", owner_id=1, task_name="app.routers.erp_converter:_job_convert", args_json="[]",
            kwargs_json="{}", status="queued", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        self.db.commit()

    def _job(self):
        self.db.expire_all()
        return self.db.query(BackgroundJob).filter_by(id="job-1").one()

    def test_a_job_waits_while_memory_is_short_then_starts_when_it_frees_up(self):
        with patch.object(memory, "available_mb", return_value=300):
            self.assertIsNone(jobs._claim_next_job_once("worker-1"))
        job = self._job()
        self.assertEqual((job.status, job.phase), ("queued", "Waiting for server memory"))
        self.assertEqual(job.attempts, 0)                          # it was not started, so no attempt is used up
        job.not_before = None                                      # the 5-second pause has passed
        self.db.commit()
        with patch.object(memory, "available_mb", return_value=9000):
            self.assertEqual(jobs._claim_next_job_once("worker-1"), "job-1")
        self.assertEqual(self._job().status, "running")

    def test_a_job_that_has_waited_long_enough_starts_even_if_memory_is_still_short(self):
        job = self._job()
        job.created_at = datetime.utcnow() - timedelta(seconds=700)
        self.db.commit()
        with patch.object(memory, "available_mb", return_value=300):
            self.assertEqual(jobs._claim_next_job_once("worker-1"), "job-1")

    def test_with_plenty_of_memory_nothing_changes(self):
        with patch.object(memory, "available_mb", return_value=15_000):
            self.assertEqual(jobs._claim_next_job_once("worker-1"), "job-1")


if __name__ == "__main__":
    unittest.main()
