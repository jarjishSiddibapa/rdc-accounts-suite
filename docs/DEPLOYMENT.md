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
listed below in MySQL Workbench. Run `start_all.bat` again: it requests a clean
shutdown from the old supervisor, removes verified orphaned suite workers,
waits for port `2805`, and re-checks dependencies before starting. It will not
kill an unrelated process that happens to own the port.

No SQL migration is required for the September 2026 launcher cleanup or ERP
streaming-writer optimization. For emergency ERP compatibility rollback, add
`ERP_CONVERTER_WRITER=openpyxl` to `backend/.env` and restart; remove it to
restore the faster default.

### SQL migrations

Run the complete contents of each required file, in date order, against the
production database. They are repeatable and do not hard-delete business data.

- `deployment/mysql/20260825_durable_concurrency.sql`
- `deployment/mysql/20260825_tab_owned_jobs.sql` (for installs that already
  had the original concurrency migration)
- `deployment/mysql/20260827_iocl_balance_monitor.sql`
- `deployment/mysql/20260828_iocl_admin_owned_sender.sql`
- `deployment/mysql/20260828_creditors_ageing_report.sql`
- `deployment/mysql/20260829_iocl_recurring_threshold_reminders.sql`
- `deployment/mysql/20260829_ultrafine_trial_balance_formatter.sql`
- `deployment/mysql/20260829_add_missing_foreign_keys.sql`
- `deployment/mysql/20260831_iocl_reminder_minutes.sql`
- `deployment/mysql/20260831_rename_rdc_payables.sql`
- `deployment/mysql/20260902_invoice_booking_tracker.sql`

If the production database is not named `rdc_accounts_suite`, change only the
database name in the `CREATE DATABASE`/`USE` statements before running a script.
The 28 August script adds the dedicated encrypted IOCL sender fields; it does
not require or copy a plaintext password.

The 31 August IOCL script adds the minute-based reminder setting. It copies the
old number unchanged, so an existing value of `30` becomes 30 minutes. The old
hours column is retained for rollback compatibility and is no longer used.

The 31 August application rename script only updates the display label for the
stable `rdc-payables` key. It does not change the schema, existing permissions,
mappings, company classification, or report data. Restarting the suite also
reconciles this canonical label, but running the script makes the production
catalogue correct before startup.

The 2 September tracker script creates the shared encrypted settings, mapping,
check-history, and notification-history tables; additively inserts the 15
reference tracker mappings; and registers the application. It never stores the
plaintext credentials from `hitanshi.docx` and never revives an archived
mapping on repeat runs. Configure credentials and recipients in the admin UI.

The foreign-key migration checks each relationship for orphaned rows before
adding its constraint and skips (printing a warning row, not an error) any
constraint whose data doesn't satisfy it yet, so it never fails outright and
never deletes or modifies business data. If Workbench's output shows a
`SKIPPED ...` warning, look at the named table's data before re-running the
script to add that one constraint later - every other constraint in the file
still gets applied normally.

### Creditors Ageing mapping transfer

Run `20260828_creditors_ageing_report.sql` once in MySQL Workbench, then restart
`start_all.bat`. Startup reads the version-controlled report template and adds
the 208 existing desktop vendor classifications that have never existed in
MySQL. This is additive and repeatable: it does not overwrite an edited row or
revive an archived row. No mapping workbook needs to be copied to production,
and there is intentionally no user-facing mapping import/export feature.

Verify the transfer in Workbench after startup:

```sql
USE `rdc_accounts_suite`;
SELECT COUNT(*) AS total_rows,
       SUM(`is_deleted` = FALSE) AS active_rows,
       SUM(`intercompany` = TRUE AND `is_deleted` = FALSE) AS active_intercompany_rows
FROM `creditors_ageing_vendor_mappings`;
```

For the initial packaged data this returns 208 active rows, including 17
intercompany rows. A later count can legitimately be different after users add
or archive mappings in the centralized application.

### Trial Balance Formatter mapping transfer

Run `20260829_ultrafine_trial_balance_formatter.sql` in MySQL Workbench and
restart `start_all.bat`. Startup additively loads the 202 packaged ledger
classifications, including six subgroup-total rows. No reference workbook is
copied to production and no mapping import/export action is exposed.

Verify after startup:

```sql
USE `rdc_accounts_suite`;
SELECT COUNT(*) AS total_rows,
       SUM(`is_deleted` = FALSE) AS active_rows,
       SUM(`is_subgroup` = TRUE AND `is_deleted` = FALSE) AS active_subgroup_rows
FROM `trial_balance_formatter_ledger_natures`;
```

For an untouched initial installation this returns 202 active rows and six
active subgroup rows. Later counts may change through the centralized editor.

## Configuration checklist

- `APP_BASE_URL` matches the LAN URL users open.
- `SESSION_COOKIE_SECURE=true` is used only when HTTPS is actually configured.
- `API_WORKERS` and `JOB_WORKER_PROCESSES` reflect available CPU/RAM.
- `ORACLE_GST_JOB_CONCURRENCY` is approved by the Oracle DBA.
- IOCL admin has verified the portal login, one dedicated sender, recipients,
  08:00 IST morning mail, interval, and threshold settings.
- Invoice Booking Tracker admin has verified DMS login, all 15 work queues,
  exact IST mail time, dedicated sender, recipients, template, and a manual
  all-pages check before enabling automation.
- Backups and audit-log retention are working.

## Post-deploy checks

1. Open `/api/health` locally.
2. Sign in as an administrator and verify the dashboard catalogue and user
   access.
3. Sign in as a regular test user and confirm only assigned applications appear.
4. Run a harmless report with representative data and confirm download.
5. On IOCL, use “Check balance now” and verify a history row; do not send a
   real test mail until SMTP recipients are confirmed.
6. On Invoice Booking Tracker, run a manual check and expand its per-location
   results. Confirm total records and pages, then send a test mail only to the
   signed-in administrator.
7. Review `backend/logs/` for tracebacks, deadlocks, or credential material.
