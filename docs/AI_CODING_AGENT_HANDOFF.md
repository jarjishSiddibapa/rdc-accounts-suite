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
5. GSTR 2B File Combinator
6. Unapplied Receipts Report
7. Ultrafine Balance Confirmation sender
8. GST Invoice Number Adder
9. Ultrafine Payment Reminder sender
10. Closing Period Report Generator
11. Ultrafine IOCL Balance Monitor
12. Ultrafine Creditors Ageing Report Generator
13. Ultrafine Trial Balance Formatter
14. Ultrafine Invoice Booking Tracker

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
Status column by header, and counts any normalized status other than `booked`.
It rejects repeated pages and validates the scanned count against the displayed
total when available. A failure in one queue aborts the whole run and mail.

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
resource key `oracle-gst`; default concurrency is one. This prevents multiple
workers from each creating the application's internal Oracle pool at the same
time. Increase `ORACLE_GST_JOB_CONCURRENCY` only after measuring Oracle and
server capacity.

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
