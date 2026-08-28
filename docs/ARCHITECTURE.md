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
