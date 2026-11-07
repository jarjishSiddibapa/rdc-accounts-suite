"""Worker counts sized from the server's cores and free memory, with .env always able to override."""

import os
import unittest
from unittest.mock import patch

from app import config, sizing


def plan(cores, free_mb, total_mb=None):
    return sizing.make_plan(cores, free_mb, total_mb)


class MakePlanTests(unittest.TestCase):
    def test_a_small_host_keeps_the_minimum_the_app_has_always_used(self):
        for cores, free in ((1, 6000), (2, 6000), (2, 3000)):
            p = plan(cores, free, 8000)
            self.assertEqual((p.api, p.jobs, p.helpers), (2, 2, 1), (cores, free))

    def test_the_documented_4_core_8gb_host_is_what_it_was_before(self):
        p = plan(4, 4500, 8000)
        self.assertEqual((p.api, p.jobs), (2, 2))
        self.assertEqual(p.helpers, 1)          # little memory to spare for extra helpers

    def test_a_4_core_server_with_lots_of_free_memory_gets_two_helpers_per_worker(self):
        p = plan(4, 11_000, 24_000)
        self.assertEqual((p.api, p.jobs, p.helpers), (2, 2, 2))

    def test_a_bigger_server_gets_more_workers_but_never_beyond_the_caps(self):
        p = plan(8, 20_000, 32_000)
        self.assertEqual((p.api, p.jobs), (3, 3))
        p = plan(32, 100_000, 128_000)
        self.assertEqual((p.api, p.jobs), (4, 4))
        self.assertLessEqual(p.helpers, 4)

    def test_memory_limits_the_processing_workers_even_with_many_cores(self):
        p = plan(16, 4_500, 16_000)             # lots of cores, but only ~2.9 GB to work with after the reserve
        self.assertEqual(p.jobs, 2)

    def test_total_helper_processes_never_exceed_the_cores_that_can_be_used(self):
        for cores in (2, 3, 4, 6, 8, 12, 16, 32):
            p = plan(cores, 60_000, 64_000)
            self.assertLessEqual(p.jobs * p.helpers, max(cores - 1, p.jobs * 2), cores)

    def test_there_are_always_at_least_two_of_each_worker_and_one_helper(self):
        p = plan(1, 100, 4_000)
        self.assertGreaterEqual(p.api, 2)
        self.assertGreaterEqual(p.jobs, 2)
        self.assertGreaterEqual(p.helpers, 1)

    def test_unreadable_memory_gives_the_documented_hosts_figures(self):
        p = plan(16, None)
        self.assertEqual((p.api, p.jobs, p.helpers), (2, 2, 1))


class OverrideTests(unittest.TestCase):
    def test_nothing_set_or_auto_leaves_it_to_the_automatic_sizing(self):
        for value in (None, "", "  ", "auto", "AUTO", "abc"):
            env = {} if value is None else {"API_WORKERS": value}
            with patch.dict(os.environ, env, clear=False):
                if value is None:
                    os.environ.pop("API_WORKERS", None)
                self.assertIsNone(sizing.configured("API_WORKERS"), value)
                self.assertEqual(sizing.setting("API_WORKERS", 3), 3, value)

    def test_a_number_set_by_hand_always_wins(self):
        with patch.dict(os.environ, {"JOB_WORKER_PROCESSES": "6", "CPU_JOB_POOL_WORKERS": "1"}):
            self.assertEqual(sizing.setting("JOB_WORKER_PROCESSES", 2), 6)
            self.assertEqual(sizing.setting("CPU_JOB_POOL_WORKERS", 4), 1)

    def test_a_zero_or_negative_number_is_at_least_one(self):
        with patch.dict(os.environ, {"API_WORKERS": "0"}):
            self.assertEqual(sizing.setting("API_WORKERS", 3), 1)

    def test_auto_in_the_env_file_cannot_crash_the_config(self):
        self.assertEqual(config._env_count("A_NAME_NOBODY_SETS", 2), 2)
        with patch.dict(os.environ, {"API_WORKERS": "auto"}):
            self.assertEqual(config._env_count("API_WORKERS", 2), 2)
        with patch.dict(os.environ, {"API_WORKERS": "5"}):
            self.assertEqual(config._env_count("API_WORKERS", 2), 5)


class DescribeTests(unittest.TestCase):
    def test_the_start_up_line_says_what_was_decided_and_why(self):
        p = plan(4, 11_049, 24_287)
        line = sizing.describe(p, 2, 2, 2)
        self.assertIn("4 CPU cores", line)
        self.assertIn("11,049 MB free", line)
        self.assertIn("2 web workers, 2 processing workers, 2 Excel helpers", line)
        self.assertIn("memory unknown", sizing.describe(plan(4, None), 2, 2, 1))


class SupervisorWiringTests(unittest.TestCase):
    """The supervisor starts as many workers as the sizing says (or .env says) and hands the helper count on."""

    def _run(self, env=None):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock
        from app import supervisor

        spawned = []
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(supervisor, "DATA_DIR", Path(tmp)), \
                patch.object(supervisor, "PID_FILE", Path(tmp) / "supervisor.pid"), \
                patch.object(supervisor, "STOP_FILE", Path(tmp) / "stop"), \
                patch.object(supervisor, "_enable_kill_on_close"), \
                patch.object(supervisor, "_disable_console_quick_edit"), \
                patch.object(supervisor.subprocess, "run"), \
                patch.object(supervisor.sizing, "auto_plan", return_value=sizing.Plan(3, 4, 2, 8, 10_000)), \
                patch.dict(os.environ, env or {}):
            for name in ("API_WORKERS", "JOB_WORKER_PROCESSES", "CPU_JOB_POOL_WORKERS"):
                if not env or name not in env:
                    os.environ.pop(name, None)

            def fake_spawn(name, command, role):
                spawned.append((name, command, role, os.environ.get("CPU_JOB_POOL_WORKERS")))
                (Path(tmp) / "stop").write_text("stop")          # end the supervisor loop at once
                return MagicMock(poll=lambda: None, wait=lambda **kw: 0)

            with patch.object(supervisor, "_spawn", fake_spawn):
                self.assertEqual(supervisor.main(), 0)
        return spawned

    def test_it_starts_what_the_sizing_chose_and_passes_the_helper_count_on(self):
        spawned = self._run()
        api = next(s for s in spawned if s[0] == "api")
        self.assertEqual(api[1][api[1].index("--workers") + 1], "3")
        self.assertEqual(sorted(s[0] for s in spawned if s[2] == "worker"), ["worker-1", "worker-2", "worker-3", "worker-4"])
        self.assertEqual({s[3] for s in spawned}, {"2"})         # every worker is started with the helper count

    def test_numbers_set_in_env_win_over_the_sizing(self):
        spawned = self._run({"API_WORKERS": "5", "JOB_WORKER_PROCESSES": "2", "CPU_JOB_POOL_WORKERS": "1"})
        api = next(s for s in spawned if s[0] == "api")
        self.assertEqual(api[1][api[1].index("--workers") + 1], "5")
        self.assertEqual(len([s for s in spawned if s[2] == "worker"]), 2)
        self.assertEqual({s[3] for s in spawned}, {"1"})

    def test_auto_in_env_means_the_sizing_decides(self):
        spawned = self._run({"API_WORKERS": "auto", "JOB_WORKER_PROCESSES": "auto"})
        self.assertEqual(len([s for s in spawned if s[2] == "worker"]), 4)


if __name__ == "__main__":
    unittest.main()
