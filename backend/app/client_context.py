"""Request-local browser-tab identity for durable background jobs.

The value comes from the frontend's per-tab ``sessionStorage`` UUID.  A
``ContextVar`` is safe across concurrent async requests and is propagated by
Starlette when synchronous route functions run in its thread pool; unlike a
module-level dictionary, it never becomes cross-request mutable state.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

from fastapi import Request

_TAB_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_current_tab_id: ContextVar[str | None] = ContextVar("current_tab_id", default=None)


def normalize_tab_id(value: str | None) -> str | None:
    candidate = (value or "").strip()
    return candidate if _TAB_ID_RE.fullmatch(candidate) else None


def current_tab_id() -> str | None:
    return _current_tab_id.get()


async def client_tab_context_middleware(request: Request, call_next):
    token = _current_tab_id.set(normalize_tab_id(request.headers.get("x-client-tab-id")))
    try:
        return await call_next(request)
    finally:
        _current_tab_id.reset(token)
