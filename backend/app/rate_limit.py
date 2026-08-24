"""Small bounded in-process sliding-window limiter for sensitive endpoints."""

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException


class SlidingWindowLimiter:
    def __init__(self, *, max_keys: int = 2_000) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def enforce(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Too many attempts. Please wait and try again.",
                    headers={"Retry-After": str(retry_after)},
                )

            events.append(now)
            if len(self._events) > self._max_keys:
                stale = [candidate for candidate, values in self._events.items() if not values or values[-1] <= cutoff]
                for candidate in stale[: max(1, len(self._events) - self._max_keys)]:
                    self._events.pop(candidate, None)


auth_limiter = SlidingWindowLimiter()
