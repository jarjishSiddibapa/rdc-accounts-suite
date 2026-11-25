# AI Coding Agent Handoff

Last reviewed: 2 September 2026

This document explains the current state of RDC Accounts Suite for future AI
coding agents. Read it before changing behavior. `README.md` remains the
operator quick start; this file records implementation decisions and traps that
are easy to miss from the UI alone.

## 1. Product and current scope

RDC Accounts Suite consolidates desktop Accounts Department utilities into one
LAN-hosted FastAPI and React application. The current suite contains:

1. ERP to Excel Converter
2. Loans & Advance, IOCL, TDS Report Generator (stable key: `rdc-payables`)
3. Unaccounted Transactions, Pending MRN, and Uninvoiced Expense PO reports
4. Trial Balance Location Wise Report
5. Ultrafine GSTR-2B Report Generator (stable key: `gstr2b-combinator`)
6. Unapplied Receipts Report
7. Ultrafine Balance Confirmation sender
8. GST Invoice Number Adder
9. Ultrafine Payment Reminder sender
10. Closing Period Report Generator
11. Ultrafine IOCL Balance Monitor
12. Ultrafine Creditors Ageing Report Generator
13. Ultrafine Trial Balance Formatter
14. Ultrafine Invoice Booking Tracker
15. RDC LC Stamper Application (stable key: `lc-stamper`)

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
- `E:\jarjish-projects\kishore-sir-closing-period-report-generator`
- `E:\jarjish-projects\hitanshi-iocl-balance-alerts`
- `E:\jarjish-projects\rakesh-sir-creditors-ageing`
- `E:\jarjish-projects\sneha-raman-dms-document-downloader` (retired from suite)
- repository-local `hitanshi.docx` (Invoice Booking Tracker workflow reference;
  contains secrets and must remain untracked)

Use the desktop source and, when practical, identical representative input to
compare parsing, mapping, report content, filenames, defaults, and mail output.
Do not claim 100% parity without actually exercising the relevant desktop and
web workflows, including real external systems when the workflow depends on
Oracle or SMTP.

The Closing Period web port lives in
`backend/app/services/closing_period/combiner.py`. Its authoritative reference
is the supplied `kishore-sir-closing-period-report-generator/script.py`: it
detects the dated Quantity/Value columns from row 2, keeps only `MOD-RM`,
`NMOD-RM`, and `STORES`, and skips the same subtotal/report rows. The port uses
the already-required `lxml` parser instead of making production depend on a
manually copied BeautifulSoup installation; `_cell_text()` deliberately mirrors
BeautifulSoup's `get_text(strip=True)` behavior. A parity run over all 225
reference exports produced the same `31-MAY-26` period, 168 processed files, 57
skipped files, and 5,206 data rows. Preserve the explicit header row before
calling `_style_main_sheet()`—that function inserts the title row, and omitting
the prewritten header silently overwrites the first business record.

The IOCL web port lives in `backend/app/services/iocl_balance/monitor.py`; its
reference is `hitanshi-iocl-balance-alerts/xtrapower_test.py`. Preserve the
reference flow: reuse Playwright storage state, fall back to credential login,
wait for the Quicklinks SPA to finish rendering, navigate through Financials
to Online CCMS Recharge, and prefer the exact CCMS Balance over the rounded
Wallet Balance. Password and Playwright storage state are Fernet-encrypted in
MySQL. Never copy the reference project's plaintext password or session JSON
into this repository, logs, job arguments, or API responses.

Each IOCL balance job makes at most three complete portal attempts before it
is recorded as failed. The first attempt may reuse the encrypted saved browser
session; later attempts deliberately use a fresh credential login. Do not turn
this into an unbounded retry loop or create a separate history row per attempt.
Below-threshold mail is time-based: send immediately on entering the configured
below-threshold range, then once per `alert_repeat_minutes` (30 minutes by default)
while the balance remains below it. Recovery to the threshold or above stops
the reminders. The legacy `alert_step_amount` column remains only for
non-destructive schema compatibility and must not drive new notifications.
Both daily and threshold email HTML bold the rendered balance value.

The Invoice Booking Tracker lives in
`backend/app/services/invoice_booking_tracker/monitor.py`; its workflow
reference is `hitanshi.docx`. The old DMS Downloader remains retired—this is a
separate narrow, read-only invoice-status automation. It scans the 15 seeded
Ultrafine invoice queues, dynamically follows every DataTables page, finds the
results grid **by its `Accounting Status` header** (`_find_status_table()`),
and counts only two normalized values there - `pending for approval` and
`submitted to accounts` - via `classify_statuses()`; every other value,
Booked included, is intentionally excluded from the count. The live table
carries three different status-like columns: a coarse `Status`
(Approved/Booked), `Accounting Status` (the field the tracker reports on),
and a separate `Workflow Status` (e.g. Rejected). Matching the wrong one
silently zeroes every count with no error - confirmed live on 2026-09-02
when the scanner's original `Status` match returned `Approved` for 100% of
rows across two sampled queues while `Accounting Status` held the real
values. Do not revert to matching a bare `status` header. Each row carries
the breakdown (`pending_for_approval`, `submitted_to_accounts`) plus their
sum (`pending`). It rejects repeated pages and validates the scanned count
against the displayed total when available. A failure in one queue aborts
the whole run and mail.

The live DMS route returns its shell before Angular/DataTables renders the
results grid. Never replace the explicit table-readiness poll with a fixed
sleep. Queue discovery likewise waits for the asynchronously inserted console
links and prefers an exact `Q` key over fuzzy label text. The scanner selects
the largest finite DataTables page size, verifies that the requested window was
actually applied, follows every remaining page, and treats the portal's
explicit `0 Workflow cases found` state as a successful zero-row queue. The
five corrected live keys (Andhra, FlyAsh Trading, Head Office, the portal's
`TELENGANA` spelling, and Visakhapatnam) are protected by the idempotent
`20260902_invoice_booking_tracker_queue_keys.sql` migration, which updates only
untouched bundled values.

The tracker follows the IOCL ownership model: one admin-owned encrypted DMS
login/session, sender, schedule, To/Cc, templates, mapping set, database lease,
and date-idempotent notification outbox. Assigned regular users can run manual
checks and read complete check/mail history, but API routes—not only the UI—
reserve configuration and mapping writes for administrators. Manual checks do
not send the scheduled daily mail. A portal attempt retries at most three times,
with attempts two and three discarding saved session state. Never put the
plaintext credentials from the reference DOCX into code, seed SQL, logs, job
arguments, documentation, or Git.

DMS enforces a single active session for this username. Live verification
proved that merely closing headless Chromium leaves the account marked logged
in, so `fetch_tracker()` must best-effort click the portal's visible logout (or
use its logout route) in `finally`. Do not remove this cleanup: the 08:00 run
must release the ID before staff arrive. A manual collision is a deliberately
safe public exception—regular users may see that the account is already in use,
while every unrelated technical error remains administrator-only.

`run_check_job`'s DB concurrency lock (`check_lock_token`/`check_lock_expires_at`)
is renewed in short windows (`_renew_lock`, `_LOCK_HEARTBEAT_WINDOW_SECONDS`,
threaded as a `heartbeat` callback through `fetch_tracker_with_retries` ->
`fetch_tracker` -> `_scan_queue`, called once per mapping and once per scanned
page) rather than reserved once for the whole worst-case run duration. A worker
process can die outright mid-scan (crash, OOM, service restart) with nothing
able to run `finally` cleanup at that point; live production hit exactly this
on 2026-09-02 and left every check "skipped" with a message that read like a
DMS conflict but was actually just this app's own orphaned lock, for up to the
old ~72-minute worst case. Renewing little and often shrinks that orphaned
window to a few minutes regardless of total run length. Do not revert to
reserving the lock for the whole run up front.

The scheduled/manual/test mail sends only the rendered HTML table in the body
- no Excel attachment. `create_workbook()` was removed entirely along with it;
do not reintroduce it without a real use site. `_table_html()` reproduces the
original manual tracker's exact colors (salmon `#F4B183` title/header banner,
peach `#FBE5D6` grand-total row, black grid lines, "-" for zero) and takes no
explicit font-family so it inherits the mail client's serif default, matching
the reference exactly. An optional per-installation `signature` column on
`invoice_booking_tracker_settings` (admin-editable, `render_templates()`'s
`signature` parameter) is appended below the table on every send using the
same escape-and-`<br>`-join pattern as `mailer_shared.py`'s per-user
signature - this is a separate, admin-owned field, not the per-user
`EmailSettings.signature` used by tools where the logged-in user sends
personally. The `/test-mail` endpoint prefers the newest real complete check
(`_latest_complete_check()`, shared with `/latest`) over synthetic
placeholder rows, falling back to the `index % 4` pattern only before any
real check has ever succeeded.

`GET /api/tools/invoice-booking-tracker/latest` returns the newest successful,
non-deleted complete snapshot. The frontend always renders this per-location
table (or an explicit pre-first-success empty state); later failed attempts do
not erase the last proven tracker. It is the same stored `result_json` used by
the scheduled email and workbook rather than a separately recomputed view.

An expired XTRAPOWER storage state can briefly redirect away from the login
route while Angular starts and then bounce back to `/account/login`. Do not
treat the first non-login URL as authentication success: `_wait_until_logged_in`
requires a stable redirect before proceeding. The portal also keeps hidden
duplicate navigation labels in its DOM, so `_click_nav` must select a genuinely
visible match and wait best-effort for the intermittent welcome modal and
`.ngx-spinner-overlay`. Otherwise the checker searches the login page's hidden
`Financials` template and reports a misleading navigation timeout. Regression
coverage lives in `backend/tests/test_iocl_balance_monitor.py`.

XTRAPOWER build 1.1.018 introduced another important login behavior: its User
ID and Password inputs render `readonly` until each receives a real click.
Playwright `fill()` alone waits for editability and eventually times out. Keep
the explicit click/wait/fill sequence in `_fill_login_field()`, and keep login
form discovery polling while the Angular shell renders. This is ordinary UI
interaction, not a CAPTCHA bypass. On 31 August 2026 both an encrypted stored
session and a completely fresh credential login were exercised against the
live portal through the exact CCMS balance page successfully.

`backend/tests/test_desktop_parity.py` currently verifies selected payables
statistics/log behavior and an Unaccounted mail attachment naming path. It is
valuable regression coverage but is not an exhaustive certification of every
desktop action.

The tool with stable key `rdc-payables` is presented to users as **Loans &
Advance, IOCL, TDS Report Generator**. Its output filename is a request field,
not a database setting. The frontend derives
`Loans and Advance, IOCL, TDS, Other till {MMM-YY} as on {DD.MM.YYYY}` from the
selected cutoff month and the current IST date, keeps it editable through the
download step, and omits the extension in the field. The API validates the
name, appends exactly one `.xlsx`, stores the submitted name in the durable job
result, and accepts a validated download-time override so a post-generation
edit controls the actual `Content-Disposition` filename. Do not rename the
`rdc-payables` key or route: existing grants depend on them.

The Creditors Ageing web port lives in
`backend/app/services/creditors_ageing/processor.py`; its reference is
`rakesh-sir-creditors-ageing/main.py`. Preserve content-based TB/Bill Wise
sheet discovery, Tally number-format-based Cr/Dr signs, report-date inference,
previous-day ageing, eight ageing buckets, mapping insertion order for the
Intercompany sheet, all five output sheets, and the unresolved-vendor flow.
`backend/seed_data/creditors-ageing-report-template.xlsx` is both the immutable
layout/formula template and the one-way source of the 208 existing vendor
classifications (17 intercompany). Runtime mappings live only in
`creditors_ageing_vendor_mappings`. Startup is additive and archive-safe. A
real parity run against the reference `build/verification_raw_28.xlsx` matched
194 TB ledgers, 1,202 Bill Wise rows, 111 Only Creditors, 66 Advances, 17
Intercompany, and the same 31 new vendors; the only cell-text differences are
the requested standardized `As on` wording. The web port additionally injects
formula caches so all live-formula values are visible before Excel recalculates.

The Trial Balance Formatter web port lives in
`backend/app/services/trial_balance_formatter/processor.py`. Its development-
only parity fixtures are `Raw Trial Balance June 2026.xlsx` and `Ultrafine Trial
Balance as on 30th June 2026.xlsx` in the repository root; they are confidential,
ignored by Git, and not required in production. The visible `June26` reference
sheet is asserted cell-by-cell when those files are present: values/formulas,
formula caches, fonts, fills, borders, number formats, alignment, merges, widths,
print orientation, and selection. The unrelated hidden historical `March26`
sheet is not derivable from the raw June input and is intentionally not copied
into newly generated workbooks.

All 202 observed ledger classifications and six subgroup-total decisions are
packaged in `backend/seed_data/trial-balance-formatter-ledger-natures.json` and
stored at runtime in `trial_balance_formatter_ledger_natures`. Treat MySQL as
authoritative. Startup seeds missing normalized keys only, never overwrites an
edit, and never revives an archived row. New ledgers are generated with an
explicit provisional result and surfaced for review rather than silently
pretending the classification is proven. Output formulas retain cached values
without Excel COM.

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

- `backend/app/startup_cleanup.py` — scoped graceful restart/orphan cleanup
- `backend/app/supervisor.py` — process lifecycle and crash restart
- `backend/app/main.py` — FastAPI app and router registration
- `backend/app/worker.py` — durable job worker loop
- `backend/app/jobs.py` — queue, leases, heartbeats, progress, actions, results
- `backend/app/scheduler_runner.py` / `scheduler.py` — single scheduler process
- `backend/app/database.py` — engine, schema initialization, additive seeds
- `backend/app/config.py` — role-aware pools and concurrency settings

`start_all.bat` does not trust only `data/supervisor.pid`. Before dependency
checks and again immediately before launch, `app.startup_cleanup` discovers
only long-running suite commands using this repository's exact virtualenv. It
requests a graceful supervisor stop through `data/supervisor.stop-requested`,
then uses checked process-tree termination only as a timeout fallback. It waits
until the scoped processes and port 2805 are both clear. An unrelated port
owner is an actionable startup error and must never be terminated. Keep the
command markers synchronized when adding another long-running process role;
do not broaden cleanup to arbitrary `python.exe` processes.

### ERP converter streaming writer

HTML-disguised ERP `.xls` reports use `DirectXlsxWriter` by default. The parser
still provides the same ordered block stream and openpyxl still builds the
small workbook/style package, but worksheet rows are emitted directly as
OOXML into a disk-backed spool. This avoids constructing an openpyxl cell
object for every report cell. Numeric XML must use openpyxl's `safe_string`
formatting so financial floats are byte-semantically equivalent after reload.

The previous optimized write-only path remains `OpenpyxlXlsxWriter`. A
`DirectXlsxWriterError` automatically reparses with it, and operators can set
`ERP_CONVERTER_WRITER=openpyxl` for an emergency rollback without a code
deployment. Do not catch `ConversionError` in that fallback: Excel row/column
limit failures are user-input limits, not OOXML assembly failures.

Real reference verification on 2 September 2026 compared all values, cell
types, number formats, fonts, fills, alignments, and borders after reload:

- 39 MB MRN input: 433,620 compared cells, identical semantic SHA-256;
- 266 MB Payables input: 3,226,824 compared cells, identical semantic SHA-256;
- both direct archives passed ZIP CRC validation; and
- Payables elapsed time was 102.6 s direct versus 162.7 s compatibility on the
  same development host. CPU-light ZIP compression makes large downloads
  somewhat bigger; that is an intentional server-processing tradeoff.

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

`background_jobs.owner_id`, `background_job_actions.owner_id`, and
`trial_balance_upload_tokens.owner_id` are real foreign keys to `users.id`;
`background_job_actions.job_id` and `background_resource_slots.job_id` are
real foreign keys to `background_jobs.id`; `application_email_recipients.
app_key` is a real foreign key to `applications.key`. These were added by
`deployment/mysql/20260829_add_missing_foreign_keys.sql` after being
validated only in the application layer for a while — see that file's
idempotent, orphan-checking pattern before adding any other implicit
relationship as a bare unconstrained column.

Request audit logging deliberately has two stages. The independent JSON-lines
trail is appended immediately, while the MySQL `audit_logs` insert is attached
to the response as a background task in `backend/app/audit_middleware.py`.
Do not move that insert back into the synchronous response path: yielding route
dependencies may still own their connections when `call_next()` returns, so a
burst can otherwise make every request wait for a second connection and
deadlock a correctly bounded SQLAlchemy pool. Preserve any response background
task that already exists (especially temporary-download cleanup) and append the
audit insert after it. Regression coverage is in
`backend/tests/test_audit_middleware.py`.

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

IOCL checks are durable detached jobs too. The scheduler queues them using
only the trigger string; workers reload encrypted portal credentials from
MySQL. A database lease prevents concurrent manual/scheduled portal checks.
Every attempted check is retained in `iocl_balance_checks`; every morning or
threshold email occurrence is retained in `iocl_balance_notifications` with
an idempotency key and delivery status. The IOCL page exposes both complete
histories with server-side filtering and pagination. The singleton IOCL
settings row also owns one dedicated sender email and encrypted app password.
Only administrators may read or change portal/session credentials, sender,
recipients, schedules, thresholds, and templates. Assigned regular users get
only the safe status summary, manual balance check, and both histories. The
API enforces this split; hiding controls in React is not sufficient. Scheduled
delivery must never depend on the last user who saved settings and must never
silently fall back to a user's personal sender or the password-reset sender.
Do not replace this evidence with process-local state or a latest-only widget.
Technical IOCL check, job, and mail-delivery errors are administrator-only.
Regular-user API responses and UI failures use the fixed public message
`We have encountered an issue, please contact Jarjish 🥲`; administrators retain
the complete stored diagnostic in status and history views.

Suite-wide frontend API errors follow the same role-aware presentation through
`frontend/src/lib/error-visibility.ts`: authenticated administrators see the
technical response, while regular and unauthenticated users see only the fixed
public message. Render failures are handled the same way by `AppErrorBoundary`.
Loading states use the shared `LoadingNotice` and the exact copy
`One sec… pretending this is very complicated 😎`; background-job phases remain
visible as secondary detail. Do not reintroduce page-specific “Loading…” copy or
display raw job/API failures directly to regular users.

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
resource key `oracle-gst`; default concurrency is two (the owner accepted that
load on the ERP; it was one before). This stops more than that many workers from
creating the application's internal Oracle pool at the same time (each job opens
several connections). The slots are created at start-up from
`ORACLE_GST_JOB_CONCURRENCY`; a value set in `.env` wins. Increase it further only
after measuring Oracle and server capacity.

The current unattended server implementation does not require Excel COM or a
local interactive Excel session. `.xlsb` handling and report generation use
Python libraries. Do not reintroduce `win32com` without a proven, explicitly
approved requirement.

Pending MRN is the one reference workflow that produced a genuine native Excel
PivotTable: `Vendorwise Pivot`. Per `vendorwise_pivot.py`'s own module
docstring, it is "the ONLY sheet across Unaccounted/MRN/PO that the source
app ever built as a real pivot; everything else there is - and stays - a
static table" (confirmed by the desktop source's own `_add_real_pivots`
comment: "Locationwise Pivot is kept as a plain openpyxl static table"). Do
not assume a template-based mechanism or a `location_incharge_period.xlsx` /
`_PivotSrc` upgrade path exists for Locationwise Pivot or any other
Unaccounted/PO sheet — no such files or code exist in this repository; if a
past revision of this handoff described one, it was aspirational and never
actually built. Verify against the code before relying on any such claim.

The current mechanism (`backend/app/services/unaccounted/vendorwise_pivot.py`)
does not use a template at all: it hand-authors the `pivotCacheDefinition`,
`pivotCacheRecords`, and `pivotTableDefinition` OOXML parts directly and
splices them into the already-saved workbook's zip, additively, alongside the
already-correct static grid `_add_vendorwise_pivot_sheet` wrote (never
touching those cells). `refreshOnLoad="1"` means any reader that does trigger
a refresh (a live Excel session) recomputes from the same underlying raw
`Summary` rows and should reach the same numbers already shown; readers that
never refresh (openpyxl, the mail-body HTML preview, Excel's Protected View)
still see correct data immediately because the grid was already right.
`excel_writers.py`'s call site wraps this in `try/except Exception`, so any
future failure here falls back to the plain static sheet rather than
corrupting the file — but see the caution below on why the corrupt file this
was meant to protect against slipped through anyway.

**Row-field axis must be built from genuinely distinct values, never from a
pre-aggregated staging table that mixed in another dimension.** A real
production report ("Pending MRN till Jul-26") came back from a user marked
`[Repaired]` in Excel's title bar, with the PivotTable silently stripped by
Excel's repair process, leaving only the (correct) static grid behind. Root
cause: `_add_vendorwise_pivot_sheet` grouped by `["Location", "SUPPLIER
NAME"]` for its staging table (one row per supplier *per location*), and
`attach_vendorwise_pivot` fed that same table's `SUPPLIER NAME` column
straight into the pivot cache's row-field `sharedItems` without
deduplicating. A supplier appearing at 17 different locations (a real
marketplace vendor) therefore produced 17 separate `<s v="...">` entries for
the identical text in a field's shared-items list — invalid per Excel's own
pivot-cache expectations (each entry must be a genuinely distinct category)
— which is exactly what triggered the repair. It also meant the *visible*
static table showed the same supplier as 17 separate partial-total rows
instead of one properly summed row, independent of the pivot bug.

The fix (see git history around `_add_vendorwise_pivot_sheet` and
`attach_vendorwise_pivot`) groups only by `["SUPPLIER NAME", "ACCOUNTING
PERIOD"]` — one row per distinct supplier, summed across every location —
matching what the reference desktop app's real PivotTable shows by default
with its Location "Report Filter" left at "(All)" (see `_add_real_pivots` in
the reference desktop source: `Location` is `XL_PAGE`, `SUPPLIER NAME` is the
only `XL_ROW` field, and `TableDestination` is cell `"A3"` — the web port's
grid now starts at column A to match, with no hidden Location column). The
reference app never hit this bug because it discards its own equivalent
staging table the instant win32com builds the real PivotTable straight from
raw `Summary` rows; the web port has no live Excel to paper over a flawed
staging table, so that table must itself already be the correct aggregate.
Per-location filtering remains the live PivotTable's Report Filter's job —
its cache is still built from the raw per-transaction `Summary` rows, each
correctly tagged with its own `Location` (see `attach_vendorwise_pivot`'s
`_PAGE_FIELD` handling, unaffected by this fix).

This class of bug (an axis meant to hold genuinely distinct values built from
something other than `.unique()`/`dict.fromkeys()` on the raw data) was
checked across every other `.pivot_table()`/`.groupby()` call in
`backend/app/services/` at the time of this fix — none of the others are
building a real Excel PivotTable cache (Vendorwise Pivot is still the only
one), and every other summary/"Main"/"Locationwise" table's row key (e.g.
`["Location", "Accounts Incharge"]` in Unaccounted's Main sheet, Locationwise
Pivot, and PO's Main sheet; `["Region", "Accounts Incharge"]` in RDC
Payables' Pivot Summary sheet) is an intentional multi-dimension breakdown
where every axis genuinely belongs in the row identity, not an incidental
dimension accidentally fragmenting what should be one row per entity. If a
similar report-summary table is added later, check that its row-grouping key
matches exactly what should visually be one row, and that any real pivot
cache's categorical axes are built from distinct source values, not a
pre-aggregated table that folds in an extra dimension.
`backend/tests/test_vendorwise_pivot.py`'s
`test_supplier_used_from_multiple_locations_collapses_to_one_row` and
`test_supplier_spanning_many_locations_opens_without_repair` are the
regression coverage for this exact failure mode - both reproduce a supplier
spanning many distinct locations and assert against a plain-openpyxl read and
a real-Excel-COM open+refresh respectively.

This has been opened and refreshed through real Excel during development
(this dev machine has pywin32 + Excel available, so
`VendorwisePivotRealExcelRoundTripTests` actually runs rather than being
skipped), but Excel/COM is not used by the deployed app.

The desktop app's real pivot also re-sorted its ACCOUNTING PERIOD column
chronologically and reformatted captions every time it ran, via COM
(`_sort_period_field`, setting each PivotItem's `.Position`/`.Caption`
explicitly) - something that cannot be replicated without a live Excel
session. The template's ACCOUNTING PERIOD field is a manual-sort field, so
Excel populates any period it hasn't seen before, on refresh, in the order it
first encounters that value scanning the `Summary` sheet top-to-bottom
(verified directly against real Excel). `write_formatted_mrn_excel` therefore
sorts its input rows chronologically by accounting period before writing
`Summary`, so the native pivot's column order still comes out chronological
regardless of the order periods appear in the uploaded MRN export. See
`test_summary_rows_are_ordered_chronologically_by_period` in
`backend/tests/test_native_pivots.py`. If a future pivot ever needs this same
treatment, apply the same source-row-ordering fix rather than assuming
`refreshOnLoad` alone reproduces the desktop's per-refresh sort/caption logic.

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

### Derived mapping fields (Accounts Incharge)

Several per-tool mapping tables store their own `accounts_incharge` column
even though a separate table in the same app already owns that value once a
Location/Region is known:

- Unaccounted: `unaccounted_site_overrides` and `unaccounted_creator_mapping`
  each carry `accounts_incharge`, but `unaccounted_location_incharge`
  (Location -> Incharge) is the master once a Location is mapped there.
- RDC Payables: `rdc_vendor_site_mapping`, `rdc_location_code_map`, and
  `rdc_invoice_override` each carry `accounts_incharge`, but
  `rdc_region_incharge_map` (Region -> Incharge) is the master once a Region
  is mapped there.

This is intentional, inherited from the original desktop apps (not a porting
mistake): the column exists so a row created before its Location/Region was
ever added to the master table still has a usable fallback value. The
invariant is that **the master always wins once it has an entry**, everywhere
that value is read or written:

- Report generation resolves it live — see `processing._resolve_incharge()`
  (Unaccounted) and `processor.py`'s "Region -> Accounts Incharge" step
  (RDC Payables) — never trusting the stored column once the master covers
  that key.
- The admin Mappings list endpoints (`GET /mappings/site-overrides`,
  `GET /mappings/creator` in `unaccounted_txn.py`; `GET /vendor-site-codes`,
  `GET /location-codes`, `GET /invoice-overrides` in `rdc_payables.py`) must
  resolve the same way before returning rows, not return the stored column
  directly — otherwise the UI shows a stale incharge after the master is
  edited even though the next report would already be correct.
- The write endpoints let the master value win over a client-supplied one
  whenever the Location/Region already has a master entry (see
  `rdc_payables.py`'s `add_vendor_site_code`/`edit_vendor_site_code` etc. and
  their inline comments) — a manually typed value only survives when there is
  no master entry yet.
- The frontend's shared `frontend/src/components/MappingTable.tsx` has a
  `deriveColumn` prop for exactly this pattern: given a row's source field
  (e.g. Location, Region), if a master lookup returns a value, the target
  field (Accounts Incharge) renders read-only and that resolved value is what
  actually gets submitted; otherwise it falls back to normal manual entry.
  Reuse this prop rather than hand-rolling another derived-field UI if a
  similar situation is found in another tool.

Trial Balance and Unapplied Receipts do not have this pattern — neither
stores Accounts Incharge on more than one table, so there is nothing to
resolve. Creditors Ageing and Trial Balance Formatter have no Location/
Region -> Incharge concept at all. Regression coverage:
`backend/tests/test_unaccounted_mappings.py`,
`backend/tests/test_rdc_payables_mappings.py`.

### IT POs Lookup (retired 2026-09-18)

This application (PO → ERP payment document number → bank statement UTR
matching) was removed from the suite — its router, service module, frontend
page, admin role picker, and tests are gone, and `"it-po-lookup"` moved from
`APP_KEYS` to `RETIRED_APP_KEYS` in `app/permissions.py` (the same soft-retire
mechanism already used for `"dms"`). Its two data tables
(`po_lookup_statement_uploads`, `po_lookup_bank_transactions`) and the
now-unused `users.po_lookup_role` column were deliberately left in the live
database rather than dropped — see
`deployment/mysql/20260918_remove_it_po_lookup.sql` for the optional,
not-yet-applied migration that actually drops them.

### RDC LC Stamper Application (`lc-stamper`)

Web port of the desktop `stamper-v2` project: finds the pages of the invoices in a list inside one or many PDFs,
checks their date and amount, stamps only those pages (or, in *Filter only* mode, just picks them out) and writes a
verification report. Page `frontend/src/pages/tools/LcStamper.tsx` (+ `components/lc-stamper/`, `lib/lcStamper.ts`),
router `app/routers/lc_stamper.py` (prefix `/api/tools/lc-stamper`), engine `app/services/lc_stamper/`.

- **Engine modules.** `targets` (invoice list: Excel/CSV/pasted, templates, stamp text placeholders), `dates`,
  `amounts`, `ocr` (page reading tiers), `analysis` (worker-side page scan), `decide` (batch-wide decisions),
  `stamping` (the single `_draw_lines` used for both the live preview sprite and the real stamp), `report`
  (xlsx with real Excel dates), `engine.run_batch` (orchestration).
- **Reading tiers, cheapest first.** (1) the PDF's own text layer, no OCR; (2) only when `ocr.page_needs_ocr` says the page
  is a scan / garbled / outlined text **and** the number was not found: the online OCR service (OCR.space-style, used
  only when `LC_OCR_ONLINE_KEY` is set; per-process circuit breaker); (3) offline RapidOCR on the models shipped in
  `app/services/lc_stamper/models/` (free, unlimited, nothing leaves the network). The top 46% of the page is OCR-ed
  first; the whole page only if needed or when amount matching is on. Modes: Smart (default) / Always OCR / Never OCR.
  Truly unlimited free online OCR does not exist: the online tier needs the operator's own key and has quotas.
  When the service answers 401/403 or an error that says the quota is used up, `ocr.shared_pause` writes
  `SCRATCH_DIR/lc-stamper/online-ocr-paused.json` (30 min), which every worker process honours, so the rest of the
  batch goes straight to offline OCR without spending more requests (a 429 pauses only 60 s; an error about one
  image, such as its size, pauses nothing). `/capabilities` reports `online_paused` and the page says so. Each worker
  sends up to `LC_ONLINE_PARALLEL` (default 3, env var) online OCR calls at once (`analysis._start_online`; pages are
  rendered in the worker's main thread, only the HTTP calls run in threads); offline OCR is unchanged.
- **`.env` keys.** `LC_OCR_ONLINE_ENABLED` (default true), `LC_OCR_ONLINE_URL` (default
  `https://api.ocr.space/parse/image`), `LC_OCR_ONLINE_KEY` (empty = online tier off; never log or commit it),
  `LC_STAMPER_RETENTION_HOURS` (24). The parent process builds `OcrConfig`; worker processes never read `.env`.
- **Rules.** The invoice number is always matched as a whole number (`1001` is not found inside `10012`). Date and
  amount are compared with the list; `match_dates` / `match_amounts` leave mismatching pages OUT, otherwise they are
  kept and flagged (`amount_tolerance` default Rs 1). Duplicates are skipped unless `keep_duplicates`. **Deliberate
  change from the desktop tool:** a rejected page no longer blocks a later good page of the same invoice. Decisions are
  batch-wide in upload order; invoices never found are reported "Not Found".
- **Parallelism.** `run_batch` scans chunks of ALL documents side by side in the job worker's private CPU pool
  (`jobs.cpu_pool()`; also `jobs.cancel_requested()` and `jobs.abort_cpu_pool()` for cancel), then one stamp task per
  document. Output is one PDF per document or one combined PDF. Several batches are simply several queued jobs
  (`app.routers.lc_stamper:_run_job` is in the `jobs._ALLOWED_TASKS` allow-list).
- **Staging.** Uploads live in `SCRATCH_DIR/lc-stamper/<user id>/` for `LC_STAMPER_RETENTION_HOURS`, outputs in
  `SCRATCH_DIR/lc-out-<uuid>/`; downloads are served only from inside that folder; document ids must match
  `^[0-9a-f]{32}$`. The `/stamp-preview` request is exempt from the global loading indicator in `lib/api.ts`.
- **Libraries (new).** `pymupdf==1.28.0` (do NOT move to 2.x: it pulls pymupdf-layout/4llm), `rapidocr==3.10.0`,
  `onnxruntime==1.32.0` (and their dependencies: opencv-python-headless, shapely, pyclipper, omegaconf, ...). The three
  ONNX models (31 MB) are committed. Run `pip install -r requirements.txt` after pulling.
- **Tests.** `tests/test_lc_stamper.py` (+ `tests/lc_stamper_helpers.py`): engine, rules, parallel batches, stamp position
  on every rotation/cropbox, offline OCR on a scanned page, fake online-OCR server and the fallback to offline, router.
  Measured speeds: `docs/lc_stamper_perf.md`.

### Ultrafine Cashflow Generator (`ultrafine-cashflow`)

Route `/tools/ultrafine-cashflow`; code in `app/services/ultrafine_cashflow/`,
`app/routers/ultrafine_cashflow.py`, `frontend/src/pages/tools/cashflow/`.
Users upload **bank statements** (never cashflows); each month that receives
new lines is regenerated into one stored workbook (Summary, Payments,
Receipts, Working), latest version per month.

- **Only three mappings**, all normalized and referenced by ID (tables
  `ufcf_*`, soft-deleted): description key -> name (`DescriptionAlias` ->
  `Party`), name -> location + CF location (on `Party`), and sharing ratios
  (`SharingGroup` / `SharingRatioSet` / `SharingRatioLine`) per payment group
  (RDC, IOCL, ETC Pool). A line's name, location and CF location are resolved
  at read time (`resolver.py`) and never stored on the transaction, so
  changing a mapping changes every month on regeneration. Per-line overrides
  are `Transaction.party_id` (another name) and `TransactionSplit` (a typed
  split over locations, which replaces the mapped allocation entirely).
- The bank's C.D. flag decides the sheet: D = Payments, C = Receipts.
- The Summary adds up **CF location** only (SUMIF over the Payments /
  Receipts CF column, in lakhs; closing = opening + payable - paid -
  statutory). Location is an informational label. CF-location Summary
  settings (order, authorized person, payable / reserve %, subtotal group)
  are edited in the Month setup tab.
- Ratio sets are chosen by **month** (stored as the first of the month) and
  carry forward until a newer set exists. Only Oct 2026 sets are seeded, so
  earlier months show "no ratio" for sharing-group names.
- Description keys (`keys.py`) strip IFSC codes, long digit tokens, dates,
  duplicate segments and the company's own name; a date/time-only key is
  rejected.
- Uploads are deduplicated (account, minute, direction, amount, description,
  occurrence) and checked against the statement header totals. A month is not
  written while any line lacks a name or CF location.
- Transaction times are naive Indian wall-clock times from the bank; the
  frontend appends `+05:30` before formatting (never treat them as UTC).
  `generated_at` fields are UTC.
- Seed (`seed_data/ultrafine-cashflow-seed.json`, built from the October
  workbook by `build_ultrafine_cashflow_seed.py`) is additive and idempotent.
  The October workbook is stored as `seed_data/ultrafine-cashflow-oct-2026.xlsx`
  and attached as the Oct 2026 download (`source = "uploaded"`) only when that
  month has no stored file; generating the month replaces it.
- Tables are created by `create_all` on start; no manual SQL migration is
  needed for a fresh deployment.

## 5. Email behavior

Default recipients and other system mail configuration are managed centrally
through the admin UI and database. They remain editable at compose time.

**One mail, however many files, the same rule in every application.** Gmail's
size limit applies to the whole email (all attachments together, about a third
bigger once encoded), not to each file; `scheduled_report_mail.MAX_ATTACHMENT_BYTES`
(17 MB of raw files) is what one mail carries in total.
`services/attachment_fit.py` (`fit_together`) is the one implementation, used by
Sales Register, Unidentified Entries, Debtors, Untagged Invoices and GST Invoice
Number: (1) files that fit together go as they are; (2) otherwise the big `.xlsx`
files are compacted, then (3) converted to `.xlsb` (same values), largest first, so
they still go in ONE mail; (4) only a file that is still over the limit on its own
is split, by the application's own splitter (Sales Register by date range, the
others by rows; every row/day lands in exactly one part, nothing is dropped); (5)
whatever must be several mails is packed into as few as possible. A mail sent in
parts keeps "(Part n of m)" in its subject but has no explanation of the split in
its text. Apps without a splitter (Debtors' Unapplied Receipts workbook, the
"Email this report" button of the report generators, Unaccounted Transactions)
shrink as above and, if still too big, stop with a clear message before anything is
sent. Cashflow attaches one small formula workbook and is not touched.

**Mail standard for every application.** Whatever an application produces, it
can be mailed by hand, to people chosen at that moment, and every send is
recorded in the application:

- Report generators with a downloadable workbook (ERP to Excel, Loans/IOCL/TDS,
  Trial Balance, GSTR 2B, Unapplied Receipts, Closing Period, Creditors Ageing,
  Trial Balance Formatter): `routers/finished_report_mail.py`
  (`make_finished_report_mail_router`, `/email/defaults|send|log`) and the
  `EmailReportCard` component beside the Download button. The finished report
  of the person's own job is attached under its download name and sent from
  their own email identity (Settings). A per-click `request_id` makes a double
  click idempotent. Default To/Cc come from the last mail, else the admin
  defaults (these apps are in `REPORT_RECIPIENT_APP_KEYS`).
- Monitors (IOCL Balance Monitor, Invoice Booking Tracker): `GET /mail/defaults`
  and `POST /mail/send`, `MailNowCard`. The latest stored check is mailed, in
  the saved wording, to the To/Cc given (the scheduled recipients are only the
  starting point), from the monitor's own sender. Recorded in the
  notification history as `manual`; a manual mail never counts as the day's
  scheduled mail (`last_daily_sent_date` / `last_scheduled_sent_date`).
- Apps built on `scheduled_report_mail` (Untagged Invoices, GST Invoice Number,
  Debtors, Sales Register, Unidentified Entries, Cashflow): preview then send,
  plus the schedule, recipients and send log where the app has a schedule.
- Unaccounted Transactions and the three bulk senders (Balance Confirmation,
  Payment Reminder, FSE Reminder): `services/send_history.py` records each send
  in the shared `scheduled_report_mail_log` (`GET /mail/log`, `SendHistoryCard`).
  Recording is best effort and can never fail a send.

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

The Unaccounted/MRN/Uninvoiced email tables are rendered from workbooks that
retain live Excel `SUBTOTAL` formulas. OpenPyXL does not calculate cached formula
results, so `mailer_shared.sheet_to_html()` reads formula cells and evaluates
the controlled `SUBTOTAL`/`SUM` forms (including comma-separated non-adjacent
ranges/cells) for HTML display only. Do not switch that renderer back to
`data_only=True`: newly generated reports would again show blank subtotal and
Grand Total cells in both preview and the sent email. The attachment itself must
remain untouched and formula-driven.

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
- every report that can stop on a missing mapping also offers **Download
  anyway** next to its "Generate report" button in the fix panel: the same
  upload is processed again with `allow_unmapped=true` (form field, JSON field
  or query parameter depending on the endpoint), the report is written with the
  unmapped values left blank (or flagged), and the browser downloads it as soon
  as the job finishes. The normal path still stops and lists what to fix. Any
  new app that blocks on mappings must add the same escape hatch. Apps that
  honour it: RDC Payables, Trial Balance, Unaccounted Transactions (Unaccounted,
  Pending MRN, Uninvoiced PO), Unapplied Receipts, Untagged Invoices, Creditors
  Ageing, GSTR-2B, Trial Balance Formatter and Ultrafine Cashflow (which
  highlights the lines, adds a Remark column and a "Not mapped" Summary row so
  totals still tie to the ledgers). The email apps send mail rather than
  produce a download, so they are not part of this;
- mapping tables, PO keywords/exclusions, users, and audit logs have usable
  search and pagination; Users and Audit Log perform filtering server-side so
  search remains correct beyond the currently visible page;
- optional first/last names are displayed when available, with email fallback;
- loading, empty, success, error, and disabled states are explicit; and
- UI polish must not weaken validation, permissions, owner isolation, or job
  idempotency.

The Bongo Cat login experiment was deliberately removed. The upstream desktop
Live2D runtime could not be reproduced in the browser with the same smoothness
and reliability without adding unnecessary client-side rendering complexity.
Do not restore a static or reduced imitation; revisit the feature only if the
product requirement explicitly changes and a faithful, stable browser
implementation is available.

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

**Worker counts are sized from the server (`app/sizing.py`, `app/memory.py`).**
When the suite starts, the supervisor works out the number of web workers
(`API_WORKERS`), processing workers (`JOB_WORKER_PROCESSES`) and Excel helpers per
processing worker (`CPU_JOB_POOL_WORKERS`, passed to every worker through the
environment) from the CPU cores and the memory free at that moment, and prints the
decision on start-up. One core is always left free; a processing worker is assumed
to need ~1.5 GB and a helper ~1 GB at their busiest; there are never fewer than 2
web or processing workers nor more than 4 of either, and at most 4 helpers. A
number set in `.env` (or `auto`, or nothing) is respected as written / decided
automatically; `start_all.bat` no longer forces 2 and 2. If memory cannot be read
the documented 4-core / 8 GB figures (2, 2, 1) are used. Before a worker
starts a queued job (`jobs._claim_next_job_once`, `jobs.memory_wait`) it checks the
memory free right now, other programs included: below `JOB_MIN_FREE_MEMORY_MB`
(default 1024; 0 = off) the job stays queued with the phase "Waiting for server
memory", and starts as soon as memory frees up or after
`JOB_MEMORY_WAIT_MAX_SECONDS` (default 600) regardless, so it can never wait
forever. The figures come from the OS (Windows API via ctypes, `/proc/meminfo`),
no extra library; an unreadable figure never delays a job. Worker and CPU counts
are otherwise unchanged and still fixed settings.

**A paused console must not stop the suite (6 Nov 2026 incident).** The suite
runs in the `start_all.bat` console window. On Windows a click inside that
window starts "Select" (QuickEdit) mode and pauses every process that next
writes to the console, so the API stopped answering and the job workers stopped
taking jobs, with nothing in the logs, while the scheduler (which writes nothing
while idle) kept queuing jobs; they all ran at once on the next restart. Two
guards: `supervisor._disable_console_quick_edit()` turns the mode off when the
suite starts, and `app/console_logging.py` makes every console log handler
(API, uvicorn, workers, scheduler) write on a background thread through a
bounded queue, so a stuck console can only drop console lines, never block a
process (file logs stay complete). Tested in `tests/test_console_logging.py`.

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

The IOCL monitor migrations are:

- `deployment/mysql/20260827_iocl_balance_monitor.sql` for the original tables;
- `deployment/mysql/20260828_iocl_admin_owned_sender.sql` for the dedicated
  admin-owned sender fields and one-time encrypted sender backfill;
- `deployment/mysql/20260829_iocl_recurring_threshold_reminders.sql` for the
  original, mistakenly hour-labelled interval;
- `deployment/mysql/20260831_iocl_reminder_minutes.sql` to add the corrected
  minute-based interval and preserve the old numeric setting unchanged.

The catalogue-only rename is
`deployment/mysql/20260831_rename_rdc_payables.sql`. It is repeatable and only
updates the display label for the stable `rdc-payables` key; it has no schema,
mapping, report-data, or permission impact.

The Creditors Ageing migration is
`deployment/mysql/20260828_creditors_ageing_report.sql`. It creates the
soft-delete mapping table and catalogue row; the following application startup
additively seeds the packaged 208 mappings. Production does not need a mapping
workbook copy or mapping import endpoint.

The Trial Balance Formatter migration is
`deployment/mysql/20260829_ultrafine_trial_balance_formatter.sql`. It creates
the soft-delete ledger classification table, safely adds `is_subgroup` to an
early development table when needed, and adds the application catalogue row.
The next startup additively seeds the packaged 202 mappings.

Existing production installs that already ran the 27 August script need only
run the 28 August script. It is idempotent and does not copy plaintext secrets.

The referential-integrity migration is
`deployment/mysql/20260829_add_missing_foreign_keys.sql`. It adds the 5
foreign keys listed in the "Durable concurrency" subsection above. It is
idempotent and defensive: before adding each constraint it checks for
orphaned rows and skips just that one constraint (printing a `SKIPPED ...`
warning row, not an error) if any are found, rather than failing the whole
script or touching business data. A warning means that one relationship's
data needs a manual look before it can be enforced; every other constraint in
the file still applies normally. This was verified with a real dry run before
being written up here, not assumed from reading the SQL.

The Untagged Invoices mapping update is
`deployment/mysql/20261007_untagged_invoices_mappings.sql`. Mapping data lives in
MySQL and does not travel with `git pull`, so this script carries the Location
Name and Accounts Incharge rows added after the original seed. It creates the
two tables if missing, inserts only absent keys (never overwriting application
edits or reviving archived rows), and changes the BANGALORE / MANGALORE incharge
only while it still holds the original `Elairaja` value. Run it in MySQL
Workbench against the production schema; it is repeatable and works before or
after the application has first started.

Untagged Invoices Report and GST Invoice Number Adder no longer take an upload:
both run the open-receivables ageing query against the Oracle ERP
(`app/services/erp_ageing.py`, the same `ORACLE_*` settings in `backend/.env`),
so they are always as on today. Generated workbooks show `-` for a zero or empty
cell (`app/services/report_dash.py`).

Both can also be mailed automatically, in any number of **mail schedules** (see "Mail schedules" below:
`app/services/mail_schedules.py`, table `mail_schedule`, created by the app at startup - no SQL script). Each
schedule has its own weekdays / times (IST), the date the report is as on, the wording, the recipients and a send log
(`scheduled_report_mail_log`); administrators change them. The scheduler process polls
`enqueue_due_checks`; scheduled mail goes out via the system email identity (or the person the schedule names).
Mailing by hand is preview-first like the Unaccounted/MRN/PO mail
(`app/routers/report_mail_routes.py`, `app/services/report_mail_preview.py`) and is the ONLY way to send a
schedule's mail now (there is no separate "Send now" run): "Preview mail" builds the report and the exact mail, the page
shows it with a download of the report, and only "Confirm & send" sends - from the signed-in person's own email
identity (Settings), logged as a manual run of that schedule.

**Location comes from the customer's ERP region** (Untagged, Unapplied Receipts
and the mailer below): `app/services/customer_region.py` reads each customer's
region from the ERP (`hz_cust_accounts` / `hz_parties.attribute5` /
`xxrdc_region_details`) and that region IS the row's Location; the stored
"Location Name -> Location" mapping is only the fallback for customers the ERP
has no region for (about 57 % of ageing rows today). The Location values are
the ERP's own 28 region names (`ERP_REGIONS`), so the stored mappings were moved
to them once (`migrate_mappings_to_erp_regions`, run from each `mapping_store.load_all`;
idempotent, keyed on an incharge row spelt exactly like a region; old rows spelt
like a region are renamed, not duplicated, because MySQL treats `MUMBAI` and
`Mumbai` as one unique key). New regions the ERP adds need only an Accounts
Incharge. The Unapplied Receipts Register is fetched from the ERP too
(`app/services/unapplied_receipts/erp_register.py`; the report's three
parameters are bound NULL; the upload is now optional).

**Payables-side Location comes from the supplier's ERP region too** (Unaccounted Transactions, Pending MRN,
Uninvoiced Expense POs, and Loans & Advance / IOCL / TDS): `app/services/supplier_region.py` reads each supplier's
region from the ERP (`ap_suppliers.segment1` -> `party_id` -> `hz_parties.attribute5` -> `xxrdc_region_details`;
about 6 % of suppliers have one) once per job (`fetch_for_job`; a failed lookup only logs a warning and the site
mapping decides every row). Order per row: the supplier's ERP region, else the Supplier Site / Vendor Site Code
mapping, else the "mapping not found" popup; explicit vendor / invoice overrides in Loans & Advance still win. The
supplier number is matched through `customer_region.normalise_account` (the export's "Supplier Number" /
"Vendor Number" column). These reports have always carried their own labels (`NCR + Delhi`, `SAMBHAJI NAGAR`,
`CAPEX`), not the ERP's, so a region is turned into that label by `LABEL_FOR_REGION` (taken from how the site
mapping already places each region's suppliers) before it is used - the two spellings land on one pivot row and the
Accounts Incharge kept against the label applies. A region that has no label keeps its ERP spelling and is warned
about if it has no incharge. The tests' `tests/conftest.py` stubs the lookup so no unit test opens a live Oracle
connection.

ERP speed: the ageing query is fetched as `ORACLE_FETCH_PARALLELISM` (4) slices
on separate connections, by `MOD(apsa.payment_schedule_id, n)`, and re-sorted by
schedule id (~20 s -> ~8 s; same rows). The register query carries a
`/*+ PARALLEL(n) */` hint (`ORACLE_REGISTER_PARALLEL_DEGREE`, 4; 0 = none),
~21 s -> ~5 s; the same hint does nothing for the ageing query (tried).

**Debtors Reports Auto Mailer** (`debtors-reports-auto-mailer`,
`app/services/debtors_mailer/`, `app/routers/debtors_mailer.py`): one mail with
the GST-numbered ageing, the Unapplied Receipts report and the Untagged Invoices
report, from ONE ageing fetch (`_SharedAgeing`), one region fetch and one
register fetch; the three reports are built side by side (threads, each writing
on the CPU pool). The default subject names the four reports in the order of the
mail body, then the date in brackets ("... Below 1k AR Invoices as on (05-Nov-2026)");
a saved row still holding the earlier default ("Debtors Reports as on {date}", see
`SUPERSEDED_SUBJECTS`) moves to it, an edited subject is left alone.
The Debtors reports are named **AR Ageing** (the ageing with the GST invoice numbers), **AR Unapplied Receipts**, **AR Untagged Invoices**
and **Below 1k AR Invoices** in the report picker, the subject, the body, the mail's table headings and the attachment file names
(`AR_Ageing_As_On_<date>.xlsx`, `AR_Unapplied_Receipts_Report_As_On_<date>.xlsx`, `AR_Untagged_Invoices_Report_As_On_<date>.xlsx`).
**Every ageing carries the GST invoice number** (the "AR Ageing" standard): the AR Ageing, and the Ageing / Untagged Ageing /
Below 1k Ageing sheets of the Untagged Invoices report, standalone and inside the Debtors mail. The look-up is one function,
`gst_invoice_adder.report_job.fetch_gst_numbers` (the open-items GST query runs while the ageing is fetched; `gst_numbers_of(df)`
for a frame already in hand); `untagged processor.add_gst_column` puts the column right before "Invoice No/ Receipt No" (blank rows
stay, with "-"), `gst_invoice_adder.processor.gst_for_rows` builds each row's key exactly as the look-up did. `untagged_job.build_report(...,
gst=)` takes the numbers (a dict or a callable) when the caller has them; the Debtors mail shares ONE look-up between the AR Ageing and
the untagged report (`debtors_mailer.auto_mail._SharedGst`). A report held for a missing mapping does not wait for it. The Unapplied
Receipts workbook lists receipts, not invoice ageing, so it has none. Any new report that shows the ageing must call the same helpers.
A renamed application's saved schedules still holding the earlier default name / subject / body word for word are moved to the new
wording at start-up (`mail_schedules.retire_superseded_wording`: only the applications listed in
`SUPERSEDED_SCHEDULE_NAMES`); wording a person edited is left alone.
The mail shows the GST counts, the Unapplied summary and the
two Untagged summaries; the workbooks are attachments, fitted into one mail by
compacting, then `.xlsb` for the largest, and only then several mails. It has its
own mapping tables (`debtors_mailer_location_map` / `_incharge_map`, seeded once
from the Untagged and Unapplied tools'), the shared automatic-mail settings and
send log (`scheduled_report_mail*`) and a preview-then-send panel, per mail schedule (below). Its jobs share the
`oracle-gst` resource allowance.

**RDC Sales Register Report** (`rdc-sales-register-report`,
`app/services/sales_register/`, `app/routers/rdc_sales_register.py`): granted like any
application (router behind `require_app_access`; everyone granted it also
manages its mail schedules - `make_schedules_router(...)` without `admin_only`; the Untagged Invoices and GST Invoice
Number schedules stay administrator-only). The Accounts Sales
Register query (`sql.py`, verbatim; org 102 fixed; the date predicate takes an
exclusive midnight after the end date) is streamed from the ERP straight into
one xlsxwriter workbook (`report.build_workbook`, run on the CPU pool): every
value as the ERP returns it, with ONE conversion: the date columns (`report._DATE_COLUMNS`: Invoice Date, GL Date,
GL Posted Date - the query returns them as `to_char` text - and the Acknowledgement Date, a text flexfield) are written
as REAL Excel dates (`dd-mmm-yyyy`, `dd-mmm-yyyy hh:mm:ss` for the acknowledgement); blanks stay blank and text that is
no date stays as it came. (Standing requirement: every application's workbook holds real Excel dates, never date text.)
Which days a report covers is `sales_register/periods.py` (pure date logic): `auto` (the month so far + the whole
previous month through the 8th), `last_month`, or a picked `range` of at most one complete month plus the first 8 days
of the next (never past yesterday), checked by `periods.check_range` for every way a period is set; the choice lives on
the schedule (`mail_schedule.period_mode/period_from/period_to`, `MailSpec.period`) and can be changed for one mail sent
by hand. The columns, headings and
order are those of the ERP report's RDC Sales Invoice Details RTF template
(`report.TEMPLATE_COLUMNS`, 50 columns; the query columns the template does not
use are left out, and Cust PAN Number is empty because the query returns no PAN). The
period is month-to-date through yesterday; on days 2-8 of a month
(`report.PREVIOUS_MONTH_THROUGH_DAY`) the whole previous month is reported too, and
ONE FILE PER MONTH: `get_report_periods` returns one period per calendar month (the
previous month, then the month so far; only the previous month on the 1st), each is its own
query and its own file (a further sheet of the same file past Excel's row limit),
never two months in one file. The month files go in one mail while together under
`MAX_ATTACHMENT_BYTES`, else one mail each. Size ladder per file
(`auto_mail.fit_in_one_file`): as is, compacted, `.xlsb`, then it stops with a
message; a month is never split. SENDER IS FIXED: `MailSpec.fixed_sender` is True, so
the report always goes from the system email (account 1, no-reply@rdc.in): saving
ignores any `sender_user_id`, a stored one is cleared (`get_or_create_settings`) and
ignored at run time, and `report_mail_routes` forces `sender="system"` for the manual
preview and confirm; the page shows it read-only (`sender_fixed`). SCHEDULE: the
usual days / times schedule (`send_mode` weekly or monthly) PLUS a second,
independent every-few-hours schedule (offered by every scheduled mailer: `MailSpec.allow_hourly` defaults True, False only for the never-scheduled Ultrafine Cashflow; `send_every_hours` 2-23,
`send_every_start`, `last_every_slot`, `send_times_enabled`, all added at startup):
round the clock, every day, in steps of N hours from the last slot started
(`hourly_anchor` sets it on save, so saving never fires a mail and the first mail is
at `send_every_start`). Either, or both, can be on (`send_times_enabled` switches the
days / times one off); `due_slot` and `due_every_slot` are checked separately, and
if both are due at once `enqueue_due_checks` starts ONE job; `next_run` is the
sooner of the two. Column headings: "IRN" (not "IRN Number"). The ERP connection is the
shared `ORACLE_*` settings, no credentials in the code. The mail text and subject
are configurable; no tables in the body.

Shared mail changes that came with it: (1) the automatic mail's schedule is now
weekly (weekdays) OR monthly (days of the month, 0 = last day) and up to 6 times
a day (`send_mode`, `send_month_days`, `send_times`, `last_attempt_slot` on
`scheduled_report_mail`, added at startup; `due_slot` / `next_run`; a missed slot
runs once, not once per missed slot); (2) the automatic mail's sender is a
setting (`sender_user_id`: None = the system email, else that person's own
email), checked on save and again at send; (3) `make_report_mail_router(...,
sender_choice=True)` lets the person pick the From of a mail sent by hand (me /
system email / another person) and the preview records it, so confirm sends from
exactly what was previewed; (4) a second admin-managed sender, the no-reply
address: `SystemEmailSettings` row id 2 (id 1 stays the platform system email),
seeded as `no-reply@rdc.in` with NO password (an administrator enters it under
Email administration, `/admin/no-reply-email*`). It is always listed as "No-reply
email" in the From choices (`scheduled_report_mail.NO_REPLY_SENDER` = -1 in
`sender_user_id`, "noreply" in the manual panel), disabled until it has a password.
The automatic-mail card also has an explicit Daily / Weekly / Monthly choice
(Daily = weekly with all seven days) and a one-sentence schedule summary.

Gmail rejects messages over 25 MB, and the full report is ~26 MB as openpyxl
writes it. `app/services/xlsx_compact.py` rewrites the same workbook without the
redundant per-cell addresses (11 MB, still a normal .xlsx that Excel opens with
no repair; verified in real Excel). The mail attaches that complete workbook;
only if even it is too big (over 17 MB) is the same workbook converted to ONE
`.xlsb` by `app/services/xlsb_writer.py`, a pure-Python writer of the binary
records ([MS-XLSB]): NO Excel, COM or other software is involved (`xlsb_convert.py`
just runs it on the CPU pool and returns False on any failure). It reads the
finished .xlsx as a stream and writes strings once in a shared table, numbers as
RK/double records, formulas as binary tokens with a computed cached value
(workbook set to recalculate on open), and the styles, widths, heights, frozen
row, autofilter and tab colours. It only converts what this app's openpyxl
workbooks use; anything else (merged cells, conditional formats, unknown
functions, ...) raises `Unsupported` so a workbook is never converted into
something different. On the 216,000 x 23 report: 11 MB compacted .xlsx -> 6 MB
.xlsb in ~27 s; all 5.2 million cells compared equal with pyxlsb, and real Excel
opens it with the same used range, values, formulas, formats, widths, frozen
header and filter (Excel was used only to check this, never at run time). Only if
the .xlsb cannot be made or is still too big is the report split into numbered
parts, in both mails, and NEVER with data left out: the Untagged mail sends the
four summary/detail sheets as part 1 and the rows of the full Ageing sheet in as
few further workbooks as fit (`auto_mail._split_without_loss`); the GST mail
splits its rows. Order: .xlsx, compacted .xlsx, .xlsb, split. A file that fits
never goes through the .xlsb writer. In a write-only openpyxl sheet the column
widths and frozen header must be set BEFORE the first row, or they are silently
ignored (numbers then show as #######).

The Untagged mail states the unit once, as a bold "Note: All amounts in Lakhs
(÷ 1,00,000)" line right after the mail text (`MailPayload.intro_note`). The mail's
summary tables carry no footnote row; the report workbook's own Untagged Summary
sheet keeps it.

Normal production update sequence:

1. `git pull origin main`
2. Apply any new documented SQL migration in MySQL Workbench.
3. Stop the old launcher cleanly after checking for active jobs.
4. Start `start_all.bat`.
5. Check `http://127.0.0.1:2805/api/health` and `backend/logs/`.

Do not rely only on SQLAlchemy `create_all` when a change needs data migration,
backfill, index replacement, or production-visible operator steps. Add an
idempotent, dated SQL script and document the exact Workbench commands.

**Fast report writing** (`app/services/fast_xlsx.py`, `xlsb_writer._feed_sheet`, `xlsx_compact.worth_compacting`):
xlsxwriter costs ~8 us a cell, so the big sheets (GST ageing, Untagged detail sheets, Sales Register) write their data rows
through `fast_xlsx.new_sheet(ws)` instead of `ws.write_*`: plain string formatting spliced into the workbook xlsxwriter
writes for everything else. It produces xlsxwriter's own sheet XML character for character (same escaping, number format,
format numbering) - `tests/test_fast_xlsx.py` compares every part of the file - and `fast_xlsx.enabled()` re-checks that once
per process on a sample, falling back to xlsxwriter itself if it ever differs (xlsxwriter is not pinned in requirements.txt).
`FAST_XLSX=0` in `.env` switches it off. The .xlsb converter reads rows made only of plain text / number / blank cells straight
from their text (`_plain_row`) and hands every other row to the XML parser as before (`tests/test_xlsb_direct_rows.py`
compares the two routes byte for byte); compaction skips files whose rows have gaps (`worth_compacting`); file replacements
retry on transient Windows locks (`io_retry.with_retry`). Measured on the dev PC: Debtors mail 167 s -> 75 s, Sales Register
110 s -> 67 s (35 s of which is the ERP query).

**Unidentified Receipt Entries Report** (shown under this name; key `unidentified-entries`, `app/services/unidentified_entries/auto_mail.py`,
`app/routers/unidentified_entries.py`): the Unapplied Receipts Register's `***** Unidentified`
receipts (each with its Bank Description, the receipt's Comments in the ERP) as an application of its
own. The rows come from `processor.split_unidentified` (shared with the Unapplied Receipts report,
whose "Unidentified Customers" sheet is unchanged) and are written by
`processor.write_unidentified_workbook` in the Accounts look (no title bar, header in row 1) to one
`Unidentified_Entries_As_On_<date>.xlsx`. It has every shared automatic-mail option (days / times,
the every-few-hours schedule, recipients, wording, send log, the as-on date, "Send now" = preview then confirm),
`fixed_sender=True` (always the system email) and, like the Sales Register, is managed by whoever is granted the
application.
Its jobs run on the `oracle-gst` resource allowance. The Debtors Reports Auto Mailer is a
`fixed_sender` report too (manual and automatic mail both from the system email) and, like the Sales
Register and Unidentified Entries, its mail schedules' settings are managed by whoever is granted the application.
Only the Untagged Invoices and GST Invoice Number mailers keep administrator-only schedules, because their sender can
be chosen.

**Mail schedules** (every scheduled mailer: Untagged Invoices Report, GST Invoice Number Adder, Debtors Reports Auto
Mailer, RDC Sales Register Report, Unidentified Entries, Ultrafine Cashflow; `app/services/mail_schedules.py`, routes
`app/routers/schedule_routes.py`, UI `components/MailSchedules.tsx`). An application has any number (at most 20) of
**mail schedules** - each one scheduled mail with its own timing, recipients, wording and send log, and where the report
can be as on a date (`MailSpec.as_on`: Untagged, GST, Debtors, Unidentified) its own as-on date, for the month-by-month
Sales Register (`MailSpec.period`) its own choice of the days it covers, and for the Debtors Reports (`MailSpec.report_choices`) its own choice of reports. They replaced the
separate "Meeting Debtors Reports Auto Mailer" application (its key is in `RETIRED_APP_KEYS`: a schedule with the
as-on date "last day of last month" is the same thing). The Ultrafine Cashflow mail is on schedules too
(`MailSpec.month`, `per_schedule`, `send_unmapped_default=False`): each schedule is of a MONTH - `current` (the month of
the run), `last` (the month before it, worked out at every run) or a picked `month` ("YYYY-MM", never one still to come):
`mail_schedule.month_mode/month_value`, `mail_schedules.resolve_month`, handed to `ultrafine_cashflow.auto_mail.build` as
`snapshot["month"]` (a hand-sent "Regenerate & mail" still passes `month=`). A scheduled run rebuilds the month
(`generate_month`) first, and holds the mail (status `held`, with the reason) when the month has no bank lines or - the
default for a new Cashflow schedule - when lines are unmapped. The page keeps its per-month "Regenerate & mail" button
(`/mail/...`, no schedule) next to the new "Mail schedules" tab (`/schedules/...`).
* **Storage**: table `mail_schedule` (`models.MailSchedule`; it began as `debtors_mail_plan` and is renamed at start-up
  by `database._rename_debtors_mail_plan_table`, keeping the rows and ids; columns `app_key`, `sender_user_id` and
  `period_mode` / `period_from` / `period_to` and `month_mode` / `month_value` are added). Its timing / run-state columns are named exactly like those of `ScheduledReportMail`, so the scheduler's
  duck-typed functions (`due_slot`, `due_every_slot`, `next_run`, ...) work on a schedule row unchanged. Recipients are the
  schedule's own `to_list` / `cc_list` (JSON), not Admin -> Report recipients (these applications are not in
  `REPORT_RECIPIENT_APP_KEYS`). `scheduled_report_mail_log.schedule_id` (the DB column kept its first name, `plan_id`) ties
  a send-log entry to its schedule. Deleting a schedule is a soft delete; it is switched off and its log stays. In the
  code and the database "schedule" was first called "plan"; the user-facing word is "mail schedule".
* **Saved together**: the schedule's one Save (`PUT /schedules/{id}/auto-mail`, `scheduled_report_mail.save_settings`)
  holds the timing, the recipients, the wording AND the content choices - `as_on_mode` (`today` = the report as it is now,
  exactly what the applications always did; `last_month_end` = the last day of the month before the run, worked out at
  every run; `date` = a fixed date, not in the future) with `as_on_date`, and `reports` (Debtors: `ageing` / `unapplied`
  / `untagged`, at least one; the mail's subject, numbered list, section numbers and "attached" sentence follow it through
  `MailPayload.reword`). The date is not a separate step or card: it is part of the scheduler and of the send panel.
  `mail_schedules.checked_content` validates, `apply_content` stores; a bad choice stops the whole save.
* **Routes** (under `/api/tools/{app}`): `/schedules` (list, create / copy), `/schedules/{id}` (get, PATCH rename, DELETE),
  and under it `/auto-mail` (+ `/log`), `/mail/defaults|preview|confirm-send|download`, `/jobs/{job}`, and for the Debtors
  Reports `/mappings/...`. There is no app-level `/auto-mail` or `/mail` any more (except the Cashflow's `/mail`). Everything
  under `/schedules/{id}` follows the application's permission (checked before the schedule is looked up, so a person
  without access cannot probe ids); creating / renaming / deleting a schedule and its `/auto-mail` follow `admin_only`
  (Untagged and GST: administrators) or the application permission (the others); listing, previewing and sending by hand
  are open to anyone granted the application. `GET /schedules` returns `can_manage`. The route factories
  (`auto_mail_routes`, `report_mail_routes`) pick the schedule up from the path with `current_schedule_id` (None for the
  Cashflow). `scheduled_report_mail.settings_view / save_settings / log_view / run_mail_job / record_manual_send` take an
  optional `schedule_id`.
* **Running**: `enqueue_due_checks` starts one `run_auto_mail_job(trigger, requested_by, schedule_id=...)` job per due
  enabled schedule of an application with `MailSpec.per_schedule` (result entries look like `<app key>#<schedule id>`).
  Each application's `build(snapshot)` reads `snapshot["as_on"]` (a date, or None) and, for the Debtors, `snapshot["reports"]`,
  filled in fresh when the run starts by `mail_schedules.scheduled_build(build, schedule_id, overrides)`, which also records
  `detail["schedule"]` / `["as_on_mode"]` / `["changed_for_this_send"]`. Two schedules run at once; the same schedule never
  runs twice at once (`running_since` per schedule). The preview and a scheduled run are the same code path.
* **As on a date per application**: Untagged fetches `erp_ageing.fetch_ageing_from_erp(..., include_buckets=False, as_on=)`
  and a `RegionFetch` and hands them to `report_job.build_report(ageing=, regions=, as_on_date=)`; GST passes
  `ageing=` (`fetch_ageing_from_erp(as_on=)`); Unidentified Entries calls `fetch_register_from_erp(as_on=)`. For "today" none
  of that is passed - the report is built exactly as before.
* **Changes for one send** ("Send now" panel): the as-on rule (and the Debtors' reports) can be changed for that one mail
  without touching the schedule. `PreviewBody.reports / as_on_mode / as_on_date` -> `mail_schedules.validate_overrides` ->
  `run_preview_job(..., overrides=...)` -> `run_options(schedule_id, overrides=...)`; the run's detail records
  `changed_for_this_send`. Recipients, subject and text are editable per send too. There is no "Send now" that skips the
  preview: the preview-then-confirm flow is the only manual send.
* **Upgrading an installation**: on the first start with schedules, `mail_schedules.migrate_all_legacy` (from `init_db`) turns
  each application's single schedule that was set up before (enabled, or with recipients, or already run) into a schedule -
  "Debtors Reports" for the Debtors, the application's name for the others - with the same days, times, recipients, sender,
  wording and the slot it last ran (so nothing is sent twice), and switches the old row off; an application never set up
  gets one schedule, switched off, so a mail can be sent by hand at once. It runs once per application (never again after a
  schedule exists, even a deleted one).
* **The page** (`MailSchedules`, used by all five pages): a schedule switcher (name, On/Off, "n of 3 reports - as on ...",
  "New schedule": blank or a copy), then, for the selected schedule (`?schedule=<id>`), one bracketed block - a header
  ("The schedule you are working on", its name, a one-sentence description, Rename / Duplicate / Delete), the schedule card
  (what it sends - reports and the as-on date -, when, who, the wording, one Save, the send history) and the "Send now" panel
  (what this mail contains, the people, subject and text; preview, then Confirm & send). Every card carries a "Schedule:
  <name>" tag. A person who may not manage a schedule (Untagged / GST, non-administrators) sees the list and the Send now
  panel only. The Debtors' two mapping tables are shared by every schedule and sit at the bottom of its page.
* **As on a past date** the ERP is asked point-in-time: `erp_ageing.ageing_as_on_sql()` takes the items with GL date <=
  the date that were not closed on it; an item still open today is counted back from today's balance (applications /
  adjustments / write-offs with a later GL date are undone), an item closed since is built forward from its original
  amount (a reversed receipt has nothing to undo), so a date after everything posted gives exactly the live ageing.
  `erp_register.register_sql(as_on)` adds `app.gl_date <= :as_on` to the Unapplied Receipts Register (applications are
  ledger entries). Checked on the live ERP: as on a far future date and as on today the rows equal the live queries row
  for row, and building forward and counting back agree for every invoice, debit memo and credit memo at four past dates.
  Only classes INV / DM / CM / PMT are counted (the only ones that are ever open).
* **An "Accounts Incharge" column right after Location on every sheet that has a Location**, and only in this
  application (the standalone Untagged Invoices, Unapplied Receipts and GST Invoice Number tools are unchanged: every
  new parameter defaults off). The Untagged workbook's three Ageing sheets get it (`build_report(...,
  incharge_column=True)`, `processor.add_incharge_column`); the Unapplied workbook's receipts and Advance sheets get
  it before Sales Person (`write_formatted_excel(..., incharge_column=True)`, `_with_incharge`); the GST ageing, which
  has only "Location Name", gets it after that column (`gst_job.build_report(..., prepare=...)`,
  `untagged processor.add_incharge_after_location_name`, the same Location as the Untagged report: the customer's ERP
  region, else the mapped Location Name). The summaries already carried the incharge. A Location with no incharge
  mapped shows "-" (blank in the Unapplied sheets, as ever). The Unidentified Customers sheet has no Location.

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

The repository now includes the operator-facing documentation set under
`docs/` (`ARCHITECTURE.md`, `DEPLOYMENT.md`, `ADMIN_GUIDE.md`, `USER_GUIDE.md`,
and `TROUBLESHOOTING.md`), plus `SECURITY.md` and `CONTRIBUTING.md`. The
screenshots in `docs/screenshots/` were captured from a disposable local
database containing only synthetic `example.invalid` identities. Keep this
documentation current when applications, migrations, or operating procedures
change. The dashboard catalogue must continue to expose all 13 active tools;
the GST Invoice Number Adder is easy to omit because it is Oracle-backed.

- `80a2f28` — added the 5 missing foreign keys described in "Durable
  concurrency" and "Database and production deployment" above
- `fd025fc` — extended live Accounts Incharge resolution to RDC Payables and
  added the frontend `deriveColumn` mechanism (see "Derived mapping fields")
- `b582342` — fixed Unaccounted's Supplier Site Overrides / Created-By tabs
  showing a stale Accounts Incharge after Location -> Incharge was edited
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
