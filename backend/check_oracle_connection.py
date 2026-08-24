"""Standalone Oracle ERP connectivity check.

Run this directly on whichever machine is having trouble (dev or
production) to find out exactly what's wrong, without going through the
web app at all:

    cd backend
    venv\\Scripts\\python.exe check_oracle_connection.py

It reads the exact same ORACLE_* settings from backend\\.env that the real
app uses (see app/config.py), reports what's actually configured (password
masked), and then tries a real connection so you get a definitive pass/
fail instead of guessing from an app-level error message.
"""

import socket
import sys

sys.path.insert(0, ".")

from app import config  # noqa: E402


def _mask(value: str) -> str:
    if not value:
        return "(not set)"
    return "*" * len(value)


def main() -> int:
    print("=" * 70)
    print("Oracle ERP connectivity check")
    print("=" * 70)

    settings = {
        "ORACLE_HOST": config.ORACLE_HOST,
        "ORACLE_PORT": config.ORACLE_PORT,
        "ORACLE_SERVICE_NAME": config.ORACLE_SERVICE_NAME,
        "ORACLE_USER": config.ORACLE_USER,
        "ORACLE_PASSWORD": _mask(config.ORACLE_PASSWORD),
        "ORACLE_INSTANT_CLIENT_DIR": config.ORACLE_INSTANT_CLIENT_DIR,
    }
    missing = []
    for key, value in settings.items():
        display = value if value else "(not set)"
        print(f"  {key:<26} = {display}")
        if not value and key != "ORACLE_PASSWORD":
            missing.append(key)
        if key == "ORACLE_PASSWORD" and value == "(not set)":
            missing.append(key)

    print()
    if missing:
        print(f"MISSING: {', '.join(missing)} - not set in backend\\.env.")
        print("Nothing will connect until these are filled in. Stopping here.")
        return 1

    # ── Step 1: is the host even reachable on that port? (rules out a typo'd
    # host/port or a firewall block before blaming Oracle credentials) ──────
    print(f"Step 1: TCP connect to {config.ORACLE_HOST}:{config.ORACLE_PORT} ...")
    try:
        with socket.create_connection((config.ORACLE_HOST, int(config.ORACLE_PORT)), timeout=8):
            print("  OK - the host/port is reachable from this machine.")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print()
        print("  This machine cannot reach that host/port at all - before anything")
        print("  Oracle-specific, check: is ORACLE_HOST/ORACLE_PORT correct, is this")
        print("  machine on the same network/VLAN as the ERP server, and is a")
        print("  firewall (Windows Firewall or a network firewall) blocking outbound")
        print("  traffic to that port from this machine?")
        return 1

    # ── Step 2: does the Oracle instant client dir exist? (thick-mode init
    # is best-effort in the app and fails silently, so check explicitly) ───
    import os
    print(f"Step 2: Oracle Instant Client dir: {config.ORACLE_INSTANT_CLIENT_DIR}")
    if os.path.isdir(config.ORACLE_INSTANT_CLIENT_DIR):
        print("  OK - directory exists.")
    else:
        print("  WARNING: this directory does not exist on this machine.")
        print("  oracledb will fall back to thin mode, which usually still works,")
        print("  but if the ERP server requires thick-mode features this could be why.")

    # ── Step 3: the real thing - an actual Oracle login ──────────────────────
    print("Step 3: Full Oracle connection + login ...")
    try:
        import oracledb
    except ImportError:
        print("  FAILED: the 'oracledb' package isn't installed in this venv.")
        return 1

    try:
        oracledb.init_oracle_client(lib_dir=config.ORACLE_INSTANT_CLIENT_DIR)
    except Exception:
        pass  # same best-effort pattern as the real app - safe to ignore

    dsn = f"{config.ORACLE_HOST}:{config.ORACLE_PORT}/{config.ORACLE_SERVICE_NAME}"
    try:
        conn = oracledb.connect(user=config.ORACLE_USER, password=config.ORACLE_PASSWORD, dsn=dsn)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM dual")
        cur.fetchone()
        conn.close()
        print("  SUCCESS - logged in and ran a test query.")
        print()
        print("Oracle ERP connectivity is fully working from this machine.")
        return 0
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print()
        print("  The network path is fine (Step 1 passed) but the login itself")
        print("  failed - double check ORACLE_USER/ORACLE_PASSWORD/ORACLE_SERVICE_NAME")
        print("  in backend\\.env are exactly right (they're case-sensitive).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
