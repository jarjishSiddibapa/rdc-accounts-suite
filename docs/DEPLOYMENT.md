# Deployment runbook

## Prerequisites

- Windows server with Python 3.11+, MySQL 8, and network access for the required
  Oracle/SMTP/IOCL services.
- A MySQL account that can create the application database and tables.
- Oracle Instant Client under `backend/instantclient/` when using Oracle tools.

## First install

1. Clone the repository on the server.
2. Copy `backend/.env.example` to `backend/.env`; set real values locally.
3. Set a strong `INITIAL_ADMIN_EMAIL` and `INITIAL_ADMIN_PASSWORD`. Remove or
   blank the initial password after the first successful startup.
4. Run `start_all.bat`. It creates the virtual environment, installs Python
   dependencies, installs Playwright Chromium, and starts the supervisor.
5. Check `http://127.0.0.1:2805/api/health` and inspect `backend/logs/`.

## Updating an existing server

```powershell
git pull origin main
```

Before restarting, check active work in the UI and apply any new migration
listed below in MySQL Workbench. Stop the old launcher cleanly, then start
`start_all.bat` again. It re-checks dependencies automatically.

### SQL migrations

Run the complete contents of each required file, in date order, against the
production database. They are repeatable and do not hard-delete business data.

- `deployment/mysql/20260825_durable_concurrency.sql`
- `deployment/mysql/20260825_tab_owned_jobs.sql` (for installs that already
  had the original concurrency migration)
- `deployment/mysql/20260827_iocl_balance_monitor.sql`
- `deployment/mysql/20260828_iocl_admin_owned_sender.sql`

If the production database is not named `rdc_accounts_suite`, change only the
database name in the `CREATE DATABASE`/`USE` statements before running a script.
The 28 August script adds the dedicated encrypted IOCL sender fields; it does
not require or copy a plaintext password.

## Configuration checklist

- `APP_BASE_URL` matches the LAN URL users open.
- `SESSION_COOKIE_SECURE=true` is used only when HTTPS is actually configured.
- `API_WORKERS` and `JOB_WORKER_PROCESSES` reflect available CPU/RAM.
- `ORACLE_GST_JOB_CONCURRENCY` is approved by the Oracle DBA.
- IOCL admin has verified the portal login, one dedicated sender, recipients,
  08:00 IST morning mail, interval, and threshold settings.
- Backups and audit-log retention are working.

## Post-deploy checks

1. Open `/api/health` locally.
2. Sign in as an administrator and verify the dashboard catalogue and user
   access.
3. Sign in as a regular test user and confirm only assigned applications appear.
4. Run a harmless report with representative data and confirm download.
5. On IOCL, use “Check balance now” and verify a history row; do not send a
   real test mail until SMTP recipients are confirmed.
6. Review `backend/logs/` for tracebacks, deadlocks, or credential material.
