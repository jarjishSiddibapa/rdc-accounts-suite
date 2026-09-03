"""Request-level audit logging middleware, plus a small one-off event logger.

Wraps every incoming request and, once the response is ready, writes one
AuditLog row for it. A logging failure must never break the actual response,
so every DB access in this file is wrapped in try/except and swallowed (with
a stderr line) rather than allowed to propagate.

main.py wires this in as a plain ASGI-style HTTP middleware function, e.g.:

    from app import audit_middleware
    app.middleware("http")(audit_middleware.audit_middleware)

(equivalently: app.add_middleware(BaseHTTPMiddleware, dispatch=audit_middleware.audit_middleware))

For one-off, non-request events (server startup/shutdown, the daily backup
job, etc.) call the module-level log_event() helper directly, e.g.:

    audit_middleware.log_event("server.start")
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from fastapi import Request
from starlette.background import BackgroundTask, BackgroundTasks

from app import auth, config
from app.database import SessionLocal
from app.models import AuditLog

logger = logging.getLogger(__name__)

_AUDIT_LOG_PATH = config.LOGS_DIR / "audit.log"


def _append_audit_file(**fields) -> None:
    """Best-effort mirror of every audit event to logs/audit.log (one JSON
    object per line) - so the trail survives independently of MySQL, per the
    "every single action, logged" requirement. Never raises: a logging
    failure must never break the actual response."""
    try:
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **fields}
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to append to audit.log", exc_info=exc)

# The frontend polls a running job's progress with a GET every ~1s, which
# would otherwise dominate the audit log with zero informational value. The
# job's *creation* request (e.g. POST /api/tools/rdc-payables/process) is a
# different path and is still logged normally - only this exact
# .../jobs/{job_id} GET shape is skipped.
_JOB_POLL_RE = re.compile(r"^/api/tools/[^/]+/jobs/[^/]+/?$")


def _resolve_actor(request: Request) -> tuple[int | None, str | None]:
    """Resolve the signed identity without adding another database query."""
    return auth.get_session_identity(request)


def _should_log(request: Request) -> bool:
    path = request.url.path
    if not path.startswith("/api/"):
        return False
    if request.method == "GET" and _JOB_POLL_RE.match(path):
        return False
    return True


def _write_db_log(
    *,
    user_id: int | None,
    actor_email: str | None,
    action: str,
    status_code: int | None,
    ip_address: str | None,
    details: dict | None = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            AuditLog(
                user_id=user_id,
                actor_email=actor_email,
                action=action,
                status_code=status_code,
                ip_address=ip_address,
                details=json.dumps(details) if details is not None else None,
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - logging must never break the response
        logger.exception("Failed to write audit log row", exc_info=exc)
    finally:
        db.close()


def _write_log(
    *,
    user_id: int | None,
    actor_email: str | None,
    action: str,
    status_code: int | None,
    ip_address: str | None,
    details: dict | None = None,
) -> None:
    """Synchronously mirror an event to the file and MySQL audit trails."""
    _append_audit_file(
        user_id=user_id,
        actor_email=actor_email,
        action=action,
        status_code=status_code,
        ip_address=ip_address,
        details=details,
    )
    _write_db_log(
        user_id=user_id,
        actor_email=actor_email,
        action=action,
        status_code=status_code,
        ip_address=ip_address,
        details=details,
    )


def _defer_request_db_log(response, **fields) -> None:
    """Attach the MySQL insert after the response without losing other tasks.

    ``call_next`` may return while a yielding endpoint dependency still owns
    its connection.  Acquiring another connection here synchronously can
    deadlock a full pool: the request cannot finish and release its endpoint
    session because audit logging is waiting for a second connection.  The
    independent file trail is written immediately; only the best-effort
    MySQL mirror is deferred until the response has been sent.
    """
    _append_audit_file(**fields)

    audit_task = BackgroundTask(_write_db_log, **fields)
    existing = response.background
    if existing is None:
        response.background = audit_task
    elif isinstance(existing, BackgroundTasks):
        existing.tasks.append(audit_task)
    else:
        response.background = BackgroundTasks([existing, audit_task])


async def audit_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        if _should_log(request):
            user_id, actor_email = _resolve_actor(request)
            _write_log(
                user_id=user_id,
                actor_email=actor_email,
                action=f"{request.method} {request.url.path}",
                status_code=500,
                ip_address=request.client.host if request.client else None,
                details={
                    "request_id": getattr(request.state, "request_id", None),
                    "duration_ms": round((time.perf_counter() - started) * 1_000, 1),
                },
            )
        raise

    try:
        if _should_log(request):
            user_id, actor_email = _resolve_actor(request)
            ip_address = request.client.host if request.client else None
            _defer_request_db_log(
                response,
                user_id=user_id,
                actor_email=actor_email,
                action=f"{request.method} {request.url.path}",
                status_code=response.status_code,
                ip_address=ip_address,
                details={
                    "request_id": getattr(request.state, "request_id", None),
                    "duration_ms": round((time.perf_counter() - started) * 1_000, 1),
                },
            )
    except Exception as exc:  # noqa: BLE001 - logging must never break the response
        logger.exception("Unexpected error while auditing request", exc_info=exc)

    return response


def log_event(action: str, user=None, details: dict | None = None) -> None:
    """One-off logger for non-request events (server startup/shutdown, the
    scheduled backup job, etc.) - opens its own short-lived session."""
    user_id = getattr(user, "id", None)
    actor_email = getattr(user, "email", None)
    _write_log(
        user_id=user_id,
        actor_email=actor_email,
        action=action,
        status_code=None,
        ip_address=None,
        details=details,
    )
