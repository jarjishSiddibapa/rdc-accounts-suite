"""Central configuration for the RDC Accounts Suite backend.

Exposes the key filesystem locations and database connection settings used
across routers and services.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> backend/
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# File-based logs, independent of the MySQL-backed audit_log table (see
# app/audit_middleware.py) - so the trail survives even if the database is
# unreachable, wiped, or being restored. app.log carries every application
# log line (startup/shutdown, scheduler ticks, tracebacks); audit.log
# mirrors every AuditLog row written, one JSON object per line.
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SEED_DIR = BASE_DIR / "seed_data"

SCRATCH_DIR = DATA_DIR / "scratch"
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY_PATH = DATA_DIR / "secret.key"

STATIC_DIR = BASE_DIR / "app" / "static"

# ── MySQL connection (all mappings/settings/users now live in MySQL, not
# SQLite or per-desktop files — kept in a gitignored .env, never committed) ──
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "rdc_accounts_suite")

# ── Oracle ERP connection (Unapplied Receipts tool only — reads Location and
# Comments live from Oracle for the current upload's receipts; never
# committed, kept in the gitignored .env alongside the MySQL credentials).
# instant_client_dir defaults to the copy already vendored under backend/ so
# a fresh checkout works with zero extra setup on the LAN host.
ORACLE_HOST = os.environ.get("ORACLE_HOST", "")
ORACLE_PORT = os.environ.get("ORACLE_PORT", "1521")
ORACLE_SERVICE_NAME = os.environ.get("ORACLE_SERVICE_NAME", "")
ORACLE_USER = os.environ.get("ORACLE_USER", "")
ORACLE_PASSWORD = os.environ.get("ORACLE_PASSWORD", "")
ORACLE_INSTANT_CLIENT_DIR = (
    os.environ.get("ORACLE_INSTANT_CLIENT_DIR", "").strip() or str(BASE_DIR / "instantclient")
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE")
ENABLE_API_DOCS = _env_bool("ENABLE_API_DOCS")

INITIAL_ADMIN_EMAIL = os.environ.get("INITIAL_ADMIN_EMAIL", "").strip().lower()
INITIAL_ADMIN_PASSWORD = os.environ.get("INITIAL_ADMIN_PASSWORD", "")

DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT_SECONDS = int(os.environ.get("DB_POOL_TIMEOUT_SECONDS", "30"))

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(600 * 1024 * 1024)))
# The desktop DMS utility did not impose a document-size ceiling.  Keep that
# behavior by default; deployments that need a guard can still opt in with a
# positive MAX_DMS_DOWNLOAD_BYTES value.
MAX_DMS_DOWNLOAD_BYTES = int(os.environ.get("MAX_DMS_DOWNLOAD_BYTES", "0"))
