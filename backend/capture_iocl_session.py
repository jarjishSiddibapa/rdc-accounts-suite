"""Standalone helper: capture a working IOCL XTRAPOWER browser session.

The IOCL Balance Monitor's automated login can get stuck behind the
portal's CAPTCHA - a real risk after several failed automated attempts in a
row (each retry looks like another suspicious login to the portal). No
password fix works around a CAPTCHA challenge; a headless browser cannot
solve one. The app already has a way around this - Settings -> "Import a
working Playwright xtrapower_session.json" - it just needs that file, which
this script produces.

Run this on a machine with a normal desktop (not the always-on server, if
that one is headless) - it opens a REAL, visible Chromium window:

    cd backend
    venv\\Scripts\\python.exe capture_iocl_session.py

Then, in that window:
    1. Log into the IOCL XTRAPOWER portal exactly as you normally would,
       solving the CAPTCHA yourself if one appears.
    2. Once you're in (you can see the dashboard/Financials menu), come
       back to this terminal and press Enter.

This saves xtrapower_session.json in the current directory. Upload that
exact file via the IOCL Balance Monitor app's Settings tab -> "Import
session". The automated checker will then reuse your real, already-past-
the-CAPTCHA session instead of trying to log in fresh every time - exactly
the same storage-state format Playwright itself reads back
(see app/services/iocl_balance/monitor.py's fetch_balance).

Nothing here touches the database or the app's stored (encrypted)
credentials - it only drives a browser and writes a local JSON file, which
a human then uploads through the normal app UI.
"""

import argparse
import sys

DEFAULT_LOGIN_URL = "https://beta.iocxtrapower.com/account/login?returnUrl=%2F"
DEFAULT_OUTPUT = "xtrapower_session.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_LOGIN_URL, help="IOCL login URL to open")
    parser.add_argument("--out", default=DEFAULT_OUTPUT, help="Where to save the session JSON")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed in this environment. Run: pip install playwright", file=sys.stderr)
        return 1

    print("=" * 70)
    print("IOCL XTRAPOWER session capture")
    print("=" * 70)
    print(f"Opening a visible browser window at:\n  {args.url}\n")
    print("Log in there (solve the CAPTCHA if one appears), then come back")
    print("here and press Enter once you're past login.\n")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)

            input("Press Enter once you are logged in... ")

            if "/account/login" in page.url.lower():
                print(
                    "\nWarning: the browser still looks like it's on the login page "
                    f"({page.url}). Saving anyway, but double-check you're actually "
                    "logged in before uploading this file.\n"
                )

            context.storage_state(path=args.out)
            print(f"Saved: {args.out}")
            print("\nNext step: IOCL Balance Monitor -> Settings -> 'Import session' -> select this file.")
        finally:
            browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
