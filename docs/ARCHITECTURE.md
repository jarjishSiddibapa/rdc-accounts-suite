# Architecture

## Runtime topology

`start_all.bat` launches one supervisor on the Windows server. The supervisor
keeps two Uvicorn API workers, two processing workers, and one scheduler alive.
FastAPI serves the committed Vite bundle, so Node is needed only to rebuild the
frontend during development.

```text
browser tabs -> API workers -> MySQL (sessions, jobs, mappings, audit)
                         -> background_jobs -> processing workers
                                             -> Oracle / SMTP / filesystem
                         scheduler --------^
```

MySQL is the shared coordination layer. Jobs, actions, leases, rate limits,
upload tokens, and resource slots must never be held only in Python memory.

### Restart boundary

The launcher discovers old suite processes by both the repository's exact
virtualenv executable and known long-running command markers. It first asks the
supervisor to shut down cleanly, allowing the already-open batch window to exit
without a false crash message. A checked process-tree kill is only the bounded
fallback for an unresponsive supervisor or orphan. Startup continues only after
the suite process set and port `2805` are clear; unrelated port owners are
reported and left untouched.

## Concurrency and ownership

Every browser processing request carries an `X-Client-Tab-ID`. The API stores
the tab lease and the worker renews/observes it while work runs. Closing a tab
abandons cancellable work; irreversible mail dispatch is detached and runs to
completion. Job status, cancellation, downloads, and one-shot actions are
owner-scoped and idempotent.

The scheduler is a single process. IOCL checks take a database lease so manual
and scheduled checks cannot overlap. Oracle-backed GST work uses the shared
`oracle-gst` slot, preventing API/worker scaling from multiplying Oracle load.

The supported deployment boundary is one Windows server with multiple local
processes. Multiple machines additionally need shared scratch/output storage
(or object storage) because MySQL alone does not make local file paths portable.

## Report identity and downloads

The public name **Loans & Advance, IOCL, TDS Report Generator** deliberately
retains the `rdc-payables` application key, API prefix, and frontend route.
Display names may evolve, but authorization keys are persistent identifiers.

Its proposed workbook name is calculated from the selected GL cutoff period and
the current IST date. The browser may submit an edited name while creating the
job and may override it again at download time. The API validates both paths,
normalizes the `.xlsx` extension, rejects Windows-reserved characters, and owns
the final `Content-Disposition` filename. This prevents a client-only rename
from bypassing server filename safety.

## Data protection

Business rows are archived with `is_deleted`; no business delete operation
physically removes them. Credentials and Playwright storage state are Fernet
encrypted in MySQL. They are loaded at send time and are never placed in job
arguments, logs, API responses, browser storage, or source control.

## Main entry points

- `backend/app/main.py`: application and router registration
- `backend/app/supervisor.py`: process lifecycle and restart handling
- `backend/app/jobs.py`: durable queue, leases, actions, and results
- `backend/app/worker.py`: processing worker loop
- `backend/app/scheduler_runner.py`: scheduled work
- `backend/app/database.py`: schema initialization and additive seed loading
- `frontend/src/App.tsx`: client routing and shell

The Creditors Ageing worker reloads its ordered vendor classification mapping
from MySQL when the job begins, then uses the packaged five-sheet workbook only
as an immutable layout/formula template. Uploaded Tally workbooks and generated
outputs remain owner/tab-scoped scratch artifacts. The job injects cached values
for its live formula cells without Excel COM, so Protected View does not show
blank totals.

The Trial Balance Formatter follows the same durable job boundary and reloads
the 202-row central ledger-nature mapping from MySQL when its worker begins.
It copies the uploaded Tally cell styles into a fresh current-period workbook,
then applies the verified Ultrafine layout, hierarchy fills, signed formulas,
and cached formula results in pure Python. The confidential raw/finished parity
pair is optional test evidence on the development machine and is not packaged
or copied to production.

The ERP Converter has a separate streaming concern: Oracle HTML exports can
contain millions of cells. Its default writer emits worksheet OOXML to a
disk-backed spool while openpyxl supplies the workbook relationships and shared
style tables. This preserves the prior workbook semantics without allocating a
`WriteOnlyCell` per value. The previous openpyxl writer remains an automatic
compatibility fallback and an environment-selectable rollback path.
