# AI Coding Agent Handoff

Last reviewed: 26 August 2026

This document explains the current state of RDC Accounts Suite for future AI
coding agents. Read it before changing behavior. `README.md` remains the
operator quick start; this file records implementation decisions and traps that
are easy to miss from the UI alone.

## 1. Product and current scope

RDC Accounts Suite consolidates desktop Accounts Department utilities into one
LAN-hosted FastAPI and React application. The current suite contains:

1. ERP to Excel Converter
2. RDC Payables Report
3. Unaccounted Transactions, Pending MRN, and Uninvoiced Expense PO reports
4. Trial Balance Location Wise Report
5. GSTR 2B File Combinator
6. Unapplied Receipts Report
7. Ultrafine Balance Confirmation sender
8. GST Invoice Number Adder
9. Ultrafine Payment Reminder sender

It also contains authentication, user and application-access administration,
central email settings/default recipients, mapping administration, audit logs,
backup scheduling, and system-maintenance controls.

The DMS Downloader was intentionally removed in commit `5b59af7`. Its reference
desktop project may still be present for historical comparison, but its API
routes, application catalogue entry, and navigation must remain absent unless
the user explicitly asks to restore it.

## 2. Reference applications and parity rule

The development machine has the original desktop projects at:

- `E:\jarjish-projects\erp-to-excel-sneha`
- `E:\jarjish-projects\sneha-raman-rdc-payables-report`
- `E:\jarjish-projects\sneha-raman-unaccounted-transactions-report`
- `E:\jarjish-projects\sneha-raman-dms-document-downloader` (retired from suite)

Use the desktop source and, when practical, identical representative input to
compare parsing, mapping, report content, filenames, defaults, and mail output.
Do not claim 100% parity without actually exercising the relevant desktop and
web workflows, including real external systems when the workflow depends on
Oracle or SMTP.

`backend/tests/test_desktop_parity.py` currently verifies selected payables
statistics/log behavior and an Unaccounted mail attachment naming path. It is
valuable regression coverage but is not an exhaustive certification of every
desktop action.

## 3. Architecture

### Runtime topology

`start_all.bat` is the only supported launcher. Do not add a second batch-file
entry point; production is intentionally configured around this one launcher.
The supervisor starts and restarts:

- two Uvicorn API workers by default (`API_WORKERS=2`);
- two durable processing workers (`JOB_WORKER_PROCESSES=2`); and
- one scheduler process.

The server listens on port `2805`. FastAPI serves the committed React bundle;
Node is not required at runtime.

Important entry points:

- `backend/app/supervisor.py` — process lifecycle and crash restart
- `backend/app/main.py` — FastAPI app and router registration
- `backend/app/worker.py` — durable job worker loop
- `backend/app/jobs.py` — queue, leases, heartbeats, progress, actions, results
- `backend/app/scheduler_runner.py` / `scheduler.py` — single scheduler process
- `backend/app/database.py` — engine, schema initialization, additive seeds
- `backend/app/config.py` — role-aware pools and concurrency settings

### Durable concurrency

Do not replace these with Python dictionaries, globals, or per-process caches.
The durable/shared runtime tables are:

- `background_jobs`
- `background_job_actions`
- `background_resource_slots`
- `rate_limit_buckets`
- `trial_balance_upload_tokens`

API processes enqueue and inspect jobs. Workers atomically claim rows with
leases, heartbeat while running, recover stale leases, persist results/errors,
and retry transient MySQL deadlocks/lock timeouts. Public API status preserves
the frontend's existing polling contract.

Job functions must be module-level and registered in the allowlist in
`backend/app/jobs.py`. Arguments/results pass through its lossless JSON codec.
Do not enqueue closures, open file objects, database sessions, Oracle
connections, or credentials. SMTP workers reload encrypted email settings by
user ID at send time; plaintext app passwords must never be serialized.

Every job lookup, cancel operation, download, and one-shot send action must
verify `owner_id`. The database action table prevents duplicate sends when two
tabs click the same action.

Browser-started processing work is additionally leased to the physical tab
that submitted it. `frontend/src/lib/api.ts` sends the per-tab
`X-Client-Tab-ID`; `backend/app/client_context.py` keeps it request-local; and
`background_jobs.client_tab_id`, `client_heartbeat_at`, and
`cancel_on_disconnect` persist ownership across all API/worker processes.
`ProgressPanel` renews the lease while polling and
`frontend/src/lib/job-lifecycle.ts` sends a keepalive abandon request when its
tab or workflow closes. Workers monitor the lease and cooperatively stop work;
the GST Oracle processor also cancels active Oracle calls and CPU subprocess
work is terminated through its worker-private pool when necessary.

Email dispatch is the exception: once sending begins, it is irreversible and
must not become a partial batch merely because a browser closes. Unaccounted,
Ultrafine Balance Confirmation, and Ultrafine Payment Reminder send tasks are
detached in `backend/app/jobs.py`, and their send `ProgressPanel` instances set
`cancelOnTabClose={false}`. Preserve both halves of that safeguard.

Trial Balance's two-step upload flow stores owner/expiry metadata in MySQL and
parsed content in UUID-named gzip JSON under the scratch directory. Do not move
its token state back into an API process.

The rate limiter uses MySQL advisory locks (`GET_LOCK` / `RELEASE_LOCK`) so the
limit is atomic across API processes. Schema initialization uses a separate
named lock (`<database>:schema-init`) so concurrent process startup cannot race.

### Oracle

Unapplied Receipts and GST Invoice Number Adder use `python-oracledb` and the
same `ORACLE_*` configuration. Client initialization is coordinated per process
by `backend/app/oracle_runtime.py`, with thin-mode fallback retained.

GST Oracle work is globally constrained by `background_resource_slots` using
resource key `oracle-gst`; default concurrency is one. This prevents multiple
workers from each creating the application's internal Oracle pool at the same
time. Increase `ORACLE_GST_JOB_CONCURRENCY` only after measuring Oracle and
server capacity.

The current unattended server implementation does not require Excel COM or a
local interactive Excel session. `.xlsb` handling and report generation use
Python libraries. Do not reintroduce `win32com` without a proven, explicitly
approved requirement.

The current supervisor targets one Windows server. Multiple processes on that
host are supported. Running workers on multiple machines would additionally
require shared scratch/output storage (or object storage); MySQL durability
alone does not make local file paths portable across machines.

## 4. Data invariants

### Soft deletion

The application's business records use `is_deleted`. Deleting means setting
that flag, keeping the row, excluding it from active queries, and permitting an
explicit restore. This includes users, applications, permissions/settings,
mappings, mail defaults, and durable runtime rows.

An HTTP `DELETE` method does not imply a SQL hard delete. Mapping and user
`DELETE` handlers intentionally archive records. Do not add `session.delete()`,
`DELETE FROM`, purge endpoints, cascade deletion, or replacement imports that
physically remove rows. Temporary scratch files and regenerated static assets
are not business records and may still be cleaned up normally.

Use the helpers in `backend/app/soft_delete.py` for keyed mapping data. A
reintroduced key may be restored only through the explicit intended flow; seed
operations must not silently revive an administrator-archived row.

### Users

- `email`: required, normalized, database-unique
- `first_name`, `last_name`: optional
- `is_active`: login/access state independent from deletion
- `is_deleted`: archive state

Never merge active/inactive and deleted/not-deleted into one flag. Preserve
application-level permissions and administrator checks.

### Central mappings

Mappings are a centralized MySQL source of truth. Mapping workbook import and
export routes were deliberately removed and are asserted absent by
`backend/tests/test_api_surface.py`.

Authoritative packaged seed data is under `backend/seed_data/`. Initialization
is additive, idempotent, preserves administrator-edited values, and does not
revive archived rows. The regression fixture deliberately starts with an
administrator-owned row and an archived row; it currently verifies that the
remaining missing-row insertions are:

- RDC Payables: 1,146 vendor-site mappings, 214 location codes, 6 row
  exclusions, 10 invoice overrides, 32 region/incharge rows, and 7 transaction
  type overrides in that fixture.
- Unaccounted: 25 site overrides, 21 creator mappings, 30 location/incharge
  rows, 4 PO keywords, 6 excluded POs, and one keyword-settings row in that
  fixture.

If seed sources change, update the packaged seed, seeding logic, and
`backend/tests/test_mapping_seeds.py` together. Never overwrite an admin's
existing row merely because a seed contains a different value.

## 5. Email behavior

Default recipients and other system mail configuration are managed centrally
through the admin UI and database. They remain editable at compose time.

For Unaccounted mail composition:

- all three reports are selected by default;
- To/CC, subject, and introductory body are prefilled;
- the subject and body change with every one-, two-, or three-report selection;
- multiple report descriptions are numbered in selection order;
- a single selected report is not numbered; and
- report output filenames and date phrasing follow the established desktop
  conventions covered by tests.

Relevant implementation and tests:

- `backend/app/services/mailer_shared.py`
- `backend/app/routers/unaccounted_txn.py`
- `backend/tests/test_mail_defaults.py`
- `frontend/src/pages/tools/UnaccountedTransactions.tsx`
- `frontend/src/components/admin/EmailSettingsSections.tsx`

Keep preview and final-send behavior separate. One-shot job actions must remain
idempotent across repeated clicks/tabs. Never persist plaintext email passwords
inside a background job.

## 6. Frontend and UX decisions

The frontend is React 19, TypeScript, Vite, Tailwind, Lucide, Motion, and local
Geist/Geist Mono font assets. The latest design-system overhaul is represented
by commit `1e6e40f`.

Preserve these UX requirements:

- professional, readable mixed-case typography; avoid blanket uppercase;
- high contrast and clear hierarchy in both light and dark themes;
- collapsible/hamburger sidebar and responsive mobile behavior;
- visible keyboard focus, semantic labels, and reduced-motion support;
- date/month/year/month-year controls use the appropriate temporal picker;
- excluded POs and similar datasets use readable lists/tables, not pill clouds;
- all mapping add/edit dialogs and missing-mapping fix panels use the shared
  searchable creatable combobox, showing existing values as the user types;
- mapping tables, PO keywords/exclusions, users, and audit logs have usable
  search and pagination; Users and Audit Log perform filtering server-side so
  search remains correct beyond the currently visible page;
- optional first/last names are displayed when available, with email fallback;
- loading, empty, success, error, and disabled states are explicit; and
- UI polish must not weaken validation, permissions, owner isolation, or job
  idempotency.

Pending MRN and Uninvoiced Expense PO uploads have a required period-detection
gate in both their standalone tabs and the combined mail workflow. The UI keeps
a persistent `idle` / `detecting` / `complete` / `failed` state, ignores stale
responses when a file is replaced or removed, displays a retryable failure, and
does not enable report generation until detection succeeds. The backend repeats
that validation before queueing a job, rejects empty detections and exclusions
from a different/stale file, and removes rejected scratch uploads. Do not turn
period detection back into a best-effort or silently swallowed step.

`frontend/vite.config.ts` writes the production bundle directly to
`backend/app/static/`. Any frontend source edit requires `npm run build`, and
both source files and the new hashed static assets must be committed together.
Old hashed assets removed by Vite are expected; do not manually preserve stale
chunks referenced by no current `index.html`.

## 7. Database and production deployment

Real credentials belong only in `backend/.env`; never commit them. Runtime data,
scratch files, backups, audit files, and the Fernet key live under
`backend/data/` and are excluded from Git. Oracle Instant Client binaries are
also excluded.

The durable-concurrency migration is:

`deployment/mysql/20260825_durable_concurrency.sql`

It is idempotent and creates the five shared runtime tables plus the initial
`oracle-gst` slot. Production operators may paste the complete script into
MySQL Workbench. If production uses a database name other than
`rdc_accounts_suite`, change its `CREATE DATABASE` and `USE` statements first.

For a production database where the original durable-concurrency migration was
already applied before tab ownership was added, also run:

`deployment/mysql/20260825_tab_owned_jobs.sql`

It idempotently adds the three browser-tab lease columns and their indexes. A
fresh environment can run the current full durable-concurrency script and then
the tab-owned script safely; both are repeatable.

Normal production update sequence:

1. `git pull origin main`
2. Apply any new documented SQL migration in MySQL Workbench.
3. Stop the old launcher cleanly after checking for active jobs.
4. Start `start_all.bat`.
5. Check `http://127.0.0.1:2805/api/health` and `backend/logs/`.

Do not rely only on SQLAlchemy `create_all` when a change needs data migration,
backfill, index replacement, or production-visible operator steps. Add an
idempotent, dated SQL script and document the exact Workbench commands.

## 8. Verification and evidence

Run from `backend/`:

```powershell
.\venv\Scripts\python.exe -m compileall -q app tests
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Run from `frontend/` after frontend changes:

```powershell
npm install
npm run build
```

The backend suite currently covers API surface decisions, access controls,
mapping seeds, mail defaults, regional conventions, selected desktop parity,
security/job isolation, durable multi-process job execution, rate limiting, and
Oracle-runtime initialization behavior.

For concurrency-sensitive changes, also test real supervised workers—not only
inline unit helpers—and verify:

- concurrent health requests succeed through multiple API workers;
- jobs for different owners cannot cross boundaries;
- multiple workers do not execute the same job/action twice;
- stale leases recover and resource slots release;
- terminating a worker causes the supervisor to restart it; and
- no new deadlock, traceback, or credential appears in runtime logs.

Before committing:

```powershell
git diff --check
git status --short
```

After pushing, verify all three SHAs match:

```powershell
git rev-parse HEAD
git rev-parse origin/main
git ls-remote origin refs/heads/main
```

## 9. Recent implementation milestones

- `1e6e40f` — frontend design-system and interaction overhaul
- `c9120fd` — durable MySQL multi-process job runtime and supervisor
- `cfd8678` — UI polish and multi-user workflow hardening
- `9c71d12` — earlier multi-user concurrency/reliability improvements
- `5b59af7` — DMS Downloader retirement
- `78f020e` / `4381818` — removal of fragile Excel COM paths and pure-Python
  spreadsheet handling improvements

Commit hashes are historical anchors, not a substitute for reading current
code. Update this handoff whenever an architectural decision, migration,
application scope, build command, or non-obvious invariant changes.
