"""Low-cost HTTP hardening, request correlation, and cache policy."""

import asyncio
import time
import uuid
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_REQUESTED_WITH = "AccountsPayablesSuite"
_MAPPING_WRITE_LOCK = asyncio.Lock()
_SERIALIZED_MAPPING_PREFIXES = (
    "/api/tools/rdc-payables/vendor-site-codes",
    "/api/tools/rdc-payables/location-codes",
    "/api/tools/rdc-payables/row-exclusions",
    "/api/tools/rdc-payables/invoice-overrides",
    "/api/tools/rdc-payables/region-incharge",
    "/api/tools/rdc-payables/transaction-type-overrides",
    "/api/tools/rdc-payables/mappings",
    "/api/tools/unaccounted/mappings",
    "/api/tools/unaccounted/po/keywords",
    "/api/tools/unaccounted/po/excluded",
)


def _same_origin(request: Request, origin: str) -> bool:
    try:
        return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()
    except ValueError:
        return False


async def security_and_performance_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "")
    if not request_id or len(request_id) > 100:
        request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()

    if request.url.path.startswith("/api/") and request.method in _UNSAFE_METHODS:
        if request.headers.get("x-requested-with") != _REQUESTED_WITH:
            return JSONResponse(
                status_code=403,
                content={"detail": "Request verification failed. Refresh the page and try again."},
            )
        origin = request.headers.get("origin")
        if origin and not _same_origin(request, origin):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked."})

    # Mapping endpoints currently load a compact map, mutate it, and write it
    # back in one request. The production launcher uses one ASGI process, so
    # serializing only these short writes prevents two LAN users from
    # overwriting each other's changes while unrelated jobs still run in
    # parallel. Database unique constraints remain the final integrity guard.
    serialize_mapping_write = (
        request.method in _UNSAFE_METHODS
        and request.url.path.startswith(_SERIALIZED_MAPPING_PREFIXES)
    )
    if serialize_mapping_write:
        async with _MAPPING_WRITE_LOCK:
            response = await call_next(request)
    else:
        response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1_000

    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'"
    )

    path = request.url.path
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    else:
        response.headers.setdefault("Cache-Control", "no-store")

    return response
