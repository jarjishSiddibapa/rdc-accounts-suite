"""FastAPI application entry point for the RDC Accounts Suite."""

import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app import audit_middleware, config, database, http_middleware
from app.routers import (
    admin_routes,
    auth_routes,
    erp_converter,
    gst_invoice_adder,
    gstr2b,
    rdc_payables,
    settings_routes,
    system_admin_routes,
    trial_balance,
    unaccounted_txn,
    unapplied_receipts,
    ultrafine_balance_confirmation,
    ultrafine_payment_reminder,
)

# Every application log line - startup/shutdown, uvicorn's own request/error
# logs, scheduler ticks, unhandled tracebacks - goes to both the console and
# a rotating file under logs/app.log, so a run that only shows a console
# briefly (double-click, Task Scheduler, a crash before anyone can read it)
# still leaves a durable trail on disk. This is separate from the granular
# per-action MySQL audit_log table (see app/audit_middleware.py), which now
# also mirrors to logs/audit.log.
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_file_handler = RotatingFileHandler(
    config.LOGS_DIR / f"app-api-{os.getpid()}.log",
    maxBytes=10_000_000,
    backupCount=10,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))

logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=[logging.StreamHandler(), _file_handler])
logger = logging.getLogger(__name__)


def _attach_file_logging_to_uvicorn() -> None:
    """uvicorn applies its own dictConfig for the "uvicorn"/"uvicorn.error"/
    "uvicorn.access" loggers during startup - AFTER this module is imported -
    which resets their handlers and sets propagate=False, silently dropping
    whatever was attached at import time. Attaching here instead, from the
    lifespan startup hook (which runs after uvicorn's own logging setup),
    is what actually sticks."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target_logger = logging.getLogger(name)
        if _file_handler not in target_logger.handlers:
            target_logger.addHandler(_file_handler)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _attach_file_logging_to_uvicorn()
    database.init_db()
    audit_middleware.log_event("server.start")
    try:
        yield
    finally:
        audit_middleware.log_event("server.stop")


app = FastAPI(
    title="RDC Accounts Suite",
    docs_url="/api/docs" if config.ENABLE_API_DOCS else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if config.ENABLE_API_DOCS else None,
    lifespan=lifespan,
)

# No CORS middleware: the built frontend is served same-origin from this
# same FastAPI process (StaticFiles mount below), and local dev proxies
# /api through Vite to this server too - so every real request is
# same-origin and a permissive CORS policy would just be unused attack
# surface for a cookie-session app.

app.middleware("http")(audit_middleware.audit_middleware)
app.middleware("http")(http_middleware.security_and_performance_middleware)
app.add_middleware(GZipMiddleware, minimum_size=1_024, compresslevel=5)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort safety net for anything a route/job didn't already turn
    into a proper HTTPException. Without this, an unexpected bug (a bare
    KeyError, an AttributeError, etc.) would leak Starlette's raw default
    500 page - including, on /api/* routes, whatever the exception's own
    str() happens to say - straight to the user. This only ever runs for
    exceptions that AREN'T already an HTTPException: FastAPI's own
    HTTPException handler is more specific and always wins for those, so
    every existing `raise HTTPException(...)` across the app keeps behaving
    exactly as before."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong. Please try again or contact support."},
        )
    raise exc


@app.get("/api/health", include_in_schema=False)
def health():
    """Trivial liveness probe - deliberately does not touch the database or
    any external service, so it still reports honestly even if MySQL/Oracle
    is down, rather than hanging on a dependency this endpoint isn't meant
    to check."""
    return {"status": "ok"}


app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(system_admin_routes.router)
app.include_router(settings_routes.router)
app.include_router(erp_converter.router)
app.include_router(rdc_payables.router)
app.include_router(unaccounted_txn.router)
app.include_router(trial_balance.router)
app.include_router(gstr2b.router)
app.include_router(unapplied_receipts.router)
app.include_router(ultrafine_balance_confirmation.router)
app.include_router(gst_invoice_adder.router)
app.include_router(ultrafine_payment_reminder.router)

# Serve the built React app's JS/CSS bundles directly.
app.mount("/assets", StaticFiles(directory=str(config.STATIC_DIR / "assets")), name="static-assets")

_INDEX_HTML = config.STATIC_DIR / "index.html"
_FAVICON_SVG = config.STATIC_DIR / "favicon.svg"


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    """Serve the suite icon outside the SPA fallback."""
    return FileResponse(_FAVICON_SVG, media_type="image/svg+xml")


# Catch-all SPA fallback, registered LAST so every /api/* route above (and
# the /assets mount) takes precedence. React Router does its own client-side
# routing, so any path that isn't an API call or a built asset - including a
# hard refresh or a shared link to e.g. /tools/rdc-payables - must still
# return index.html and let the SPA take over, not a raw 404.
@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
    return FileResponse(_INDEX_HTML)
