"""Console log output that can never stall the process that writes it.

The suite runs in a console window (start_all.bat). On Windows a console that is paused (somebody clicked inside
the window, which starts "Select" mode; the Pause key; a stuck remote session) blocks every process that next
writes to it, and a log line is such a write. The API then stops answering, the job workers stop taking jobs, and
nothing is logged to say why, because the file log sits behind the same blocked write.

So the console handler only puts the record on a bounded in-memory queue and a background thread does the real
write. If the console stalls, only that thread waits (records are dropped once the queue is full); the API, the
workers and the scheduler carry on, and the file logs stay complete."""

import logging
import queue
from logging.handlers import QueueHandler, QueueListener

_QUEUE_SIZE = 10_000


class NonBlockingHandler(QueueHandler):
    """Hands records to a thread that writes them to the wrapped handler; never blocks, never raises."""

    def __init__(self, target: logging.Handler):
        self._records: queue.Queue = queue.Queue(_QUEUE_SIZE)
        super().__init__(self._records)
        self.setLevel(target.level)
        self.target = target
        self._listener = QueueListener(self._records, target, respect_handler_level=True)
        self._listener.start()           # a daemon thread: it never keeps the process alive

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self._records.put_nowait(record)
        except queue.Full:
            pass                         # the console is stuck: drop the console copy, keep running

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record                    # the target handler's own formatter needs the original args


def non_blocking(handler: logging.Handler) -> logging.Handler:
    """``handler`` (typically a console StreamHandler) made non-blocking; file handlers are returned as they are."""
    if isinstance(handler, (NonBlockingHandler, logging.FileHandler)) or not isinstance(handler, logging.StreamHandler):
        return handler
    return NonBlockingHandler(handler)


def make_loggers_non_blocking(*logger_names: str) -> None:
    """Replace the console handlers already attached to the named loggers (uvicorn installs its own)."""
    for name in logger_names:
        target = logging.getLogger(name)
        for handler in list(target.handlers):
            wrapped = non_blocking(handler)
            if wrapped is not handler:
                target.removeHandler(handler)
                target.addHandler(wrapped)
