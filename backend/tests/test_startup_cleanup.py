"""Regression coverage for start_all's verified previous-run cleanup."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import startup_cleanup


class StartupCleanupSelectionTests(unittest.TestCase):
    def setUp(self):
        self.project_python = str(
            Path(startup_cleanup.BACKEND_DIR / "venv" / "Scripts" / "python.exe").resolve()
        )

    def _process(self, pid, executable=None, command="python -m app.worker"):
        return startup_cleanup.ProcessInfo(
            pid=pid,
            parent_pid=1,
            executable=executable or self.project_python,
            command_line=command,
        )

    def test_only_exact_project_venv_processes_are_selected(self):
        project = self._process(101)
        unrelated = self._process(
            202,
            executable=r"C:\Other Project\venv\Scripts\python.exe",
            command="python -m app.worker",
        )
        selected = startup_cleanup._suite_processes([project, unrelated], own_pid=999)
        self.assertEqual(selected, [project])

    def test_project_venv_maintenance_command_is_not_killed(self):
        pip_install = self._process(404, command="python -m pip install -r requirements.txt")
        tests = self._process(405, command="python -m unittest discover")
        self.assertEqual(
            startup_cleanup._suite_processes([pip_install, tests], own_pid=999),
            [],
        )

    def test_cleanup_process_never_selects_itself(self):
        current = self._process(303, command="python -m app.startup_cleanup")
        self.assertEqual(
            startup_cleanup._suite_processes([current], own_pid=303),
            [],
        )

    def test_supervisor_detection_is_command_line_specific(self):
        supervisor = self._process(1, command="python -m app.supervisor")
        worker = self._process(2, command="python -m app.worker --name worker-1")
        self.assertTrue(startup_cleanup._is_supervisor(supervisor))
        self.assertFalse(startup_cleanup._is_supervisor(worker))


class StartupCleanupFlowTests(unittest.TestCase):
    def test_graceful_stop_marker_is_preferred_to_force_kill(self):
        supervisor = startup_cleanup.ProcessInfo(
            10, 1, str(next(iter(startup_cleanup.VENV_PYTHONS))), "python -m app.supervisor"
        )
        stop_file = Mock()
        with (
            patch.object(startup_cleanup, "_list_processes", side_effect=[[supervisor], []]),
            patch.object(startup_cleanup, "STOP_FILE", stop_file),
            patch.object(startup_cleanup.time, "sleep"),
        ):
            remaining = startup_cleanup._request_graceful_supervisor_stop(
                [supervisor], wait_seconds=2
            )

        self.assertEqual(remaining, [])
        stop_file.write_text.assert_called_once_with("restart\n", encoding="utf-8")

    def test_supervisor_tree_is_terminated_before_orphan(self):
        supervisor = startup_cleanup.ProcessInfo(
            10, 1, str(next(iter(startup_cleanup.VENV_PYTHONS))), "python -m app.supervisor"
        )
        orphan = startup_cleanup.ProcessInfo(
            11, 1, str(next(iter(startup_cleanup.VENV_PYTHONS))), "python -m app.worker"
        )
        snapshots = [
            [supervisor, orphan],
            [orphan],
            [],
        ]
        terminated = []

        def list_processes():
            return snapshots.pop(0) if snapshots else []

        with (
            patch.object(startup_cleanup, "_list_processes", side_effect=list_processes),
            patch.object(startup_cleanup, "_terminate_tree", side_effect=terminated.append),
            patch.object(startup_cleanup, "_port_owners", return_value=[]),
            patch.object(startup_cleanup, "_read_registered_pid", return_value=10),
            patch.object(
                startup_cleanup,
                "_request_graceful_supervisor_stop",
                return_value=[supervisor],
            ),
            patch.object(startup_cleanup, "PID_FILE", Mock()),
            patch.object(startup_cleanup, "STOP_FILE", Mock()),
            patch.object(startup_cleanup.time, "sleep"),
        ):
            count = startup_cleanup.cleanup_previous_suite(timeout_seconds=2)

        self.assertEqual(count, 2)
        self.assertEqual(terminated, [10, 11])

    def test_unrelated_port_owner_fails_without_termination(self):
        with (
            patch.object(startup_cleanup, "_list_processes", return_value=[]),
            patch.object(startup_cleanup, "_port_owners", return_value=[777]),
            patch.object(startup_cleanup, "_read_registered_pid", return_value=None),
            patch.object(startup_cleanup, "_terminate_tree") as terminate,
            patch.object(startup_cleanup, "PID_FILE", Mock()),
            patch.object(startup_cleanup, "STOP_FILE", Mock()),
            patch.object(startup_cleanup.time, "sleep"),
        ):
            with self.assertRaises(startup_cleanup.CleanupError) as ctx:
                startup_cleanup.cleanup_previous_suite(timeout_seconds=1)

        self.assertIn("unrelated process", str(ctx.exception))
        terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
