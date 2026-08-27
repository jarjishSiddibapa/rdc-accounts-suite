# RDC Accounts Suite

A single LAN web application consolidating RDC Concrete and Ultrafine's Accounts
Department tools — previously separate Windows desktop apps — into one FastAPI
backend + React frontend, running as supervised API/worker processes on one
office PC and shared over the local network via a browser.

## Apps included

- ERP to Excel Converter
- RDC Payables Report
- Unaccounted Transactions, Pending MRN & Uninvoiced Expense POs Report Generator
- Trial Balance Location Wise Report Generator
- GSTR 2B File Combinator
- Unapplied Receipts Report Generator
- Ultrafine Balance Confirmation Bulk Sender
- Ultrafine Bulk Payment Reminder Sender
- GST Invoice Number Adder
- Closing Period Report Generator
- Ultrafine IOCL Balance Monitor (scheduled morning and threshold alert emails)

Plus shared admin features: user management, per-app access control, email
sender settings, database backup scheduling, and a file-based + database
audit log. Mapping inputs show searchable existing-value suggestions, and the
admin Users/Audit Log screens use server-side search and pagination.

## Stack

- **Backend:** FastAPI (Python), SQLAlchemy, and a durable MySQL job queue.
  Two API workers, two bounded report-processing workers, and one scheduler
  are supervised independently without requiring Celery or Redis.
- **Frontend:** React + Vite + TypeScript + Tailwind, built to static files
  and served directly by FastAPI. No Node process at runtime.
- Windows deployment integration: Oracle Instant Client (thick-mode
  `oracledb`, for the Unapplied Receipts and GST Invoice Adder tools). Report
  generation and `.xlsb` handling are pure Python and do not require Excel COM
  or an interactive Microsoft Excel session. The IOCL monitor uses a
  headless Playwright Chromium browser; `start_all.bat` installs/checks that
  browser runtime automatically.

## First-time setup

1. **MySQL** — have a MySQL server reachable from this machine, with an
   account that can create databases (the app creates its own database and
   tables on first run).
2. **Backend config** — copy `backend/.env.example` to `backend/.env` and
   fill in real values (MySQL credentials, `INITIAL_ADMIN_EMAIL` /
   `INITIAL_ADMIN_PASSWORD` for the first login, `APP_BASE_URL`, and the
   Oracle connection details if you'll use the Oracle-backed tools).
   `backend/.env` is gitignored — it holds real secrets and is never
   committed.
3. **Oracle Instant Client** (only needed for Unapplied Receipts / GST
   Invoice Number Adder) — download the Basic package for Windows from
   Oracle's site and extract it to `backend/instantclient/`. It's excluded
   from git because its DLLs are hundreds of MB (over GitHub's 100MB
   per-file limit), so this is a one-time manual step per machine.
4. **Run it** — double-click `start_all.bat`. This is the suite's only
   launcher. First run creates a Python virtual environment; every run
   (including the first) checks
   `requirements.txt` against what's installed and installs anything
   missing before initializing MySQL and starting the server at
   `http://<this-pc's-LAN-IP>:2805` —
   so a `git pull` that adds a new dependency is picked up automatically on
   the next restart, no manual `pip install` needed. Logs go to
   `backend/logs/`.

The frontend is pre-built and committed under `backend/app/static/`, so
`start_all.bat` needs only Python at runtime — no Node/npm install on the
production machine.

## Making a frontend change

If you edit anything under `frontend/`, rebuild it before deploying:

```bash
cd frontend
npm install
npm run build
```

`npm run build` writes straight into `backend/app/static/`, which is what gets
committed and served when `start_all.bat` launches the FastAPI processes.

## AI coding agents

Future coding agents must read `AGENTS.md` and
`docs/AI_CODING_AGENT_HANDOFF.md` before changing the application. Those files
record the soft-delete, mapping, concurrency, Oracle, email, parity, frontend
build, testing, and deployment decisions that are not obvious from the UI.

## Deploying an update to the production PC

This repo uses **manual deploys** — no CI/CD. On the production machine:

```bash
git pull
```

apply any new SQL file documented under `deployment/`, then stop and restart
`start_all.bat` (it re-checks its virtual
environment and reinstalls dependencies automatically if `requirements.txt`
changed). If only backend files changed, a restart is enough; if frontend
files changed, make sure the build in `backend/app/static/` was committed
before pulling (see above).

For the 25 August 2026 concurrency release, existing production databases must
run `deployment/mysql/20260825_tab_owned_jobs.sql` in MySQL Workbench after the
original durable-concurrency migration. It is safe to run more than once.

For the 27 August 2026 IOCL monitor release, run
`deployment/mysql/20260827_iocl_balance_monitor.sql` in MySQL Workbench. It is
idempotent and creates the monitor settings, complete check history, durable
notification history, and application catalogue row without storing any
plaintext credentials.

## What's not in this repo

- `backend/data/` — the running database's working files, scratch
  uploads/outputs, backups, and the Fernet key used to encrypt the stored
  email app-password. Generated at runtime; never committed.
- `backend/instantclient/` — see step 3 above.
- `backend/.env` — real secrets; use `.env.example` as the template.
