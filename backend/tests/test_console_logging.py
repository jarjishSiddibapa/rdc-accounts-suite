"""A paused console window must not be able to freeze the API, the workers or the scheduler."""

import logging
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app import console_logging, supervisor


class _BlockedConsole(logging.StreamHandler):
    """A console that is paused until released, like a window in Select mode."""

    def __init__(self):
        super().__init__(stream=MagicMock())
        self.gate = threading.Event()
        self.written: list[str] = []

    def emit(self, record):
        self.gate.wait(timeout=10)
        self.written.append(record.getMessage())


def _logger(handler, name):
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def _wait_for(condition, seconds=3.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class NonBlockingTests(unittest.TestCase):
    def test_logging_does_not_wait_for_a_paused_console(self):
        console = _BlockedConsole()
        logger = _logger(console_logging.NonBlockingHandler(console), "test.console.blocked")
        started = time.perf_counter()
        for index in range(50):
            logger.info("line %s", index)
        self.assertLess(time.perf_counter() - started, 1.0)          # the caller was never held up
        self.assertEqual(console.written, [])                          # while the console is still paused
        console.gate.set()
        self.assertTrue(_wait_for(lambda: len(console.written) == 50))
        self.assertEqual(console.written, [f"line {i}" for i in range(50)])   # nothing lost, in order

    def test_records_are_dropped_not_blocked_when_the_console_stays_stuck(self):
        console = _BlockedConsole()
        with patch.object(console_logging, "_QUEUE_SIZE", 5):
            handler = console_logging.NonBlockingHandler(console)
        logger = _logger(handler, "test.console.full")
        started = time.perf_counter()
        for index in range(100):
            logger.info("line %s", index)
        self.assertLess(time.perf_counter() - started, 1.0)
        console.gate.set()
        self.assertTrue(_wait_for(lambda: len(console.written) >= 5))
        self.assertLessEqual(len(console.written), 6)                  # the queue plus the one in flight

    def test_the_targets_own_formatter_still_sees_the_original_arguments(self):
        # uvicorn's access formatter reads record.args (client, method, path, status): they must survive
        seen = []

        class ArgsFormatter(logging.Formatter):
            def format(self, record):
                seen.append(record.args)
                return super().format(record)

        console = logging.StreamHandler(stream=MagicMock())
        console.setFormatter(ArgsFormatter("%(message)s"))
        logger = _logger(console_logging.NonBlockingHandler(console), "test.console.args")
        logger.info('%s - "%s %s" %d', "10.0.0.1:5000", "GET", "/x", 200)
        self.assertTrue(_wait_for(lambda: bool(seen)))
        self.assertEqual(seen[0], ("10.0.0.1:5000", "GET", "/x", 200))

    def test_a_traceback_is_still_written(self):
        console = logging.StreamHandler(stream=MagicMock())
        written = []
        console.emit = lambda record: written.append(console.format(record))
        logger = _logger(console_logging.NonBlockingHandler(console), "test.console.trace")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("it failed")
        self.assertTrue(_wait_for(lambda: bool(written)))
        self.assertIn("ValueError: boom", written[0])

    def test_file_handlers_are_left_alone_and_console_ones_are_wrapped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            file_handler = logging.FileHandler(f"{tmp}/x.log", delay=True)
            self.assertIs(console_logging.non_blocking(file_handler), file_handler)
            console = logging.StreamHandler(stream=MagicMock())
            wrapped = console_logging.non_blocking(console)
            self.assertIsInstance(wrapped, console_logging.NonBlockingHandler)
            self.assertIs(console_logging.non_blocking(wrapped), wrapped)         # not wrapped twice

    def test_loggers_that_already_have_a_console_handler_are_switched_over(self):
        # uvicorn installs its own console handlers; they are replaced, anything else is kept
        logger = logging.getLogger("test.console.swap")
        console = logging.StreamHandler(stream=MagicMock())
        other = logging.NullHandler()
        logger.handlers = [console, other]
        console_logging.make_loggers_non_blocking("test.console.swap")
        self.assertNotIn(console, logger.handlers)
        self.assertIn(other, logger.handlers)
        self.assertTrue(any(isinstance(h, console_logging.NonBlockingHandler) for h in logger.handlers))


class QuickEditTests(unittest.TestCase):
    def _run(self, os_name="nt", console=True):
        kernel32 = MagicMock()

        def get_mode(_handle, ref):
            ref._obj.value = 0x01F7              # QuickEdit (0x40) on, as in a default console window
            return 1 if console else 0

        kernel32.GetConsoleMode.side_effect = get_mode
        kernel32.SetConsoleMode.return_value = 1
        windll = MagicMock(kernel32=kernel32)
        with patch.object(supervisor.os, "name", os_name), patch("ctypes.windll", windll, create=True):
            result = supervisor._disable_console_quick_edit()
        return result, kernel32

    def test_quick_edit_is_switched_off_and_the_other_console_modes_are_kept(self):
        result, kernel32 = self._run()
        self.assertTrue(result)
        new_mode = kernel32.SetConsoleMode.call_args.args[1]
        self.assertEqual(new_mode & 0x0040, 0)            # QuickEdit off
        self.assertNotEqual(new_mode & 0x0080, 0)         # extended flags on, or the change is ignored
        self.assertEqual(new_mode & 0x01B7, 0x01B7)       # everything else unchanged

    def test_nothing_happens_without_a_console_or_off_windows(self):
        result, kernel32 = self._run(console=False)
        self.assertFalse(result)
        kernel32.SetConsoleMode.assert_not_called()
        result, kernel32 = self._run(os_name="posix")
        self.assertFalse(result)
        kernel32.GetConsoleMode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
