"""MySQL-backed sliding-window limiter shared by every API process."""

import hashlib
import json
import time
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.database import engine
from app.models import RateLimitBucket


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SlidingWindowLimiter:
    def __init__(self, *, max_keys: int = 2_000) -> None:
        # Retained for API compatibility; rows are soft-deleted by maintenance
        # rather than evicted in-process, so the limit is no longer per worker.
        self._max_keys = max_keys

    def enforce(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.time()
        cutoff = now - window_seconds
        lock_name = "rate-limit:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
        blocked = False
        retry_after = 1

        # GET_LOCK is connection-scoped. Keep this exact connection until the
        # bucket transaction commits, then release it in finally. Requests for
        # different keys remain parallel; only the same login/IP key serializes.
        connection = engine.connect()
        acquired = False
        try:
            acquired = connection.execute(
                text("SELECT GET_LOCK(:lock_name, 5)"),
                {"lock_name": lock_name},
            ).scalar() == 1
            connection.commit()
            if not acquired:
                raise HTTPException(
                    status_code=503,
                    detail="Authentication is busy. Please retry in a moment.",
                    headers={"Retry-After": "1"},
                )

            transaction = connection.begin()
            try:
                connection.execute(
                    mysql_insert(RateLimitBucket)
                    .values(
                        limiter_key=key,
                        events_json="[]",
                        updated_at=_utcnow(),
                        is_deleted=False,
                    )
                    .prefix_with("IGNORE")
                )
                row = connection.execute(
                    select(RateLimitBucket.events_json)
                    .where(RateLimitBucket.limiter_key == key)
                    .with_for_update()
                ).one()
                events = [float(value) for value in json.loads(row.events_json or "[]")]
                events = [value for value in events if value > cutoff]
                if len(events) >= limit:
                    blocked = True
                    retry_after = max(1, int(window_seconds - (now - events[0])))
                else:
                    events.append(now)
                connection.execute(
                    update(RateLimitBucket)
                    .where(RateLimitBucket.limiter_key == key)
                    .values(
                        events_json=json.dumps(events, separators=(",", ":")),
                        updated_at=_utcnow(),
                        is_deleted=False,
                    )
                )
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
        finally:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT RELEASE_LOCK(:lock_name)"),
                        {"lock_name": lock_name},
                    )
                    connection.commit()
                except Exception:
                    connection.invalidate()
            connection.close()

        if blocked:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(retry_after)},
            )


auth_limiter = SlidingWindowLimiter()
