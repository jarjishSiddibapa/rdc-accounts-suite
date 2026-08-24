# RDC Accounts Suite

A single LAN web application consolidating RDC Concrete and Ultrafine's Accounts
Department tools — previously separate Windows desktop apps — into one FastAPI
backend + React frontend, running as one Python process on one office PC and
shared over the local network via a browser.

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

Plus shared admin features: user management, per-app access control, email
sender settings, database backup scheduling, and a file-based + database
audit log.

## Stack

- **Backend:** FastAPI (Python), SQLAlchemy, MySQL, a small in-process
  `ThreadPoolExecutor` job queue (no Celery/Redis) — sized for a modest office
  PC, not a cloud server.
- **Frontend:** React + Vite + TypeScript + Tailwind, built to static files
  and served directly by FastAPI. No Node process at runtime.
- Windows-only integrations used by a few tools: `win32com` (Excel COM
  automation for pivot tables) and Oracle Instant Client (thick-mode
  `oracledb`, for the Unapplied Receipts and GST Invoice Adder tools).

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
4. **Run it** — double-click `start_server.bat`. First run creates a Python
   virtual environment and installs dependencies automatically; every run
   after that just starts the server at `http://<this-pc's-LAN-IP>:2805`.
   Logs go to `backend/logs/`.

The frontend is pre-built and committed under `backend/app/static/`, so
`start_server.bat` needs only Python at runtime — no Node/npm install on the
production machine.

## Making a frontend change

If you edit anything under `frontend/`, rebuild it before deploying:

```bash
cd frontend
npm install
npm run build
```

`npm run build` writes straight into `backend/app/static/`, which is what
gets committed and what `start_server.bat` serves.

## Deploying an update to the production PC

This repo uses **manual deploys** — no CI/CD. On the production machine:

```bash
git pull
```

then stop and restart `start_server.bat` (it re-checks its virtual
environment and reinstalls dependencies automatically if `requirements.txt`
changed). If only backend files changed, a restart is enough; if frontend
files changed, make sure the build in `backend/app/static/` was committed
before pulling (see above).

## What's not in this repo

- `backend/data/` — the running database's working files, scratch
  uploads/outputs, backups, and the Fernet key used to encrypt the stored
  email app-password. Generated at runtime; never committed.
- `backend/instantclient/` — see step 3 above.
- `backend/.env` — real secrets; use `.env.example` as the template.
