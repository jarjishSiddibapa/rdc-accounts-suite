"""Process-wide coordination for python-oracledb client initialization.

Both Oracle-backed applications share the same Python process.  Thick-mode
initialization is process-global and may only be attempted once, so concurrent
jobs (or routers importing at the same time) must not race that operation.
If Instant Client is unavailable, preserving the established best-effort
behaviour lets python-oracledb continue in thin mode.
"""

from __future__ import annotations

import threading
from typing import Any


_oracle_init_lock = threading.Lock()
_oracle_init_attempted = False


def initialize_oracle_client(driver: Any, instant_client_dir: str) -> None:
    """Attempt process-wide Oracle client initialization exactly once."""
    global _oracle_init_attempted
    if _oracle_init_attempted:
        return
    with _oracle_init_lock:
        if _oracle_init_attempted:
            return
        # Set this before invoking the driver so even a failed thick-mode
        # attempt cannot be raced/repeated by another application thread.
        _oracle_init_attempted = True
        try:
            driver.init_oracle_client(lib_dir=instant_client_dir)
        except Exception:
            # The suite has always allowed python-oracledb thin-mode fallback.
            pass
