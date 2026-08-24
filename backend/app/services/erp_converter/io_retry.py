"""Retry helper for transient Windows file-access errors.

`[Errno 13] Permission denied` on a freshly-produced ERP export is almost
always transient on Windows, not a real permissions problem - the two
common causes are antivirus real-time scanning holding an exclusive lock
on a just-downloaded/just-written file for a moment, or the destination
file still being held open by Excel/another process. Retrying briefly
resolves the antivirus case entirely and gives the user a clear, actionable
message for the "still open elsewhere" case instead of a bare errno.
"""
import time

_ATTEMPTS = 6
_INITIAL_DELAY = 0.5
_BACKOFF = 1.6


def with_retry(func, path, action):
    """Call func() (a zero-arg callable), retrying on PermissionError with
    exponential backoff. `path`/`action` (e.g. "read", "write") are used
    only to produce a clear message if every retry is exhausted.
    """
    delay = _INITIAL_DELAY
    last_exc = None
    for attempt in range(_ATTEMPTS):
        try:
            return func()
        except PermissionError as exc:
            last_exc = exc
            if attempt == _ATTEMPTS - 1:
                break
            time.sleep(delay)
            delay *= _BACKOFF
    raise PermissionError(
        f"Cannot {action} '{path}' - permission denied after retrying for a few "
        f"seconds. It's likely open in Excel or another program, or still being "
        f"scanned by antivirus - close it (or wait a moment) and try again. "
        f"({last_exc})"
    ) from last_exc
