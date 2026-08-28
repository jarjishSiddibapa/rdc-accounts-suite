# AI Coding Agent Instructions

Read [docs/AI_CODING_AGENT_HANDOFF.md](docs/AI_CODING_AGENT_HANDOFF.md) before
changing this repository. It is the maintained source of truth for the current
architecture, product invariants, reference applications, migrations, testing,
and deployment workflow.

## Start every task this way

1. Run `git status --short --branch` and preserve work you did not create.
2. Read the relevant router, service, model, frontend page, and tests before
   editing. Do not infer desktop parity from a similar filename or UI.
3. If Graphify is installed and `graphify-out/graph.json` exists, use
   `graphify query` for codebase questions. When the user explicitly types
   `/graphify`, invoke the Graphify skill before doing anything else.
4. Keep changes scoped, add or update tests, rebuild generated frontend assets
   when frontend source changes, and report what was actually verified.

## Non-negotiable product invariants

- The application uses soft deletion. Business/database records are archived
  with `is_deleted = true`; never hard-delete them. An HTTP `DELETE` route may
  exist, but its implementation must archive the row and allow restoration.
- User email is required, normalized, and unique. `first_name` and `last_name`
  are optional. Preserve separate `is_active` and `is_deleted` states.
- Mappings live centrally in MySQL. Do not restore mapping workbook
  import/export endpoints. Seeds add only missing rows, preserve administrator
  edits, and must not revive archived rows implicitly.
- Never put SMTP/app passwords or other plaintext secrets in queued-job JSON,
  logs, API responses, source control, or frontend storage.
- Never introduce process-local mutable state for jobs, rate limits, upload
  tokens, mail actions, or other cross-request state. The app deliberately runs
  multiple API and job-worker processes.
- `start_all.bat` is the only supported launcher. It starts the supervisor for
  API workers, job workers, and the scheduler; do not add another batch-file
  entry point.
- A job/status/action lookup must be owner-scoped. One user or browser tab must
  never see, cancel, download, or send another user's result.
- Browser-submitted processing jobs are also tab-owned through
  `X-Client-Tab-ID` and a MySQL heartbeat lease. Closing or navigating away
  from that tab abandons its cancellable work; irreversible email dispatch
  jobs are deliberately detached and must continue safely server-side.
- Oracle work must honor the shared `oracle-gst` resource slots and must not
  multiply Oracle connection pools merely because API/worker counts increase.
- The IOCL monitor has one dedicated, admin-owned sender and one shared
  configuration. Only administrators may change portal credentials/session,
  sender credentials, recipients, schedules, thresholds, or templates.
  Assigned regular users may check the balance and read check/notification
  history, but configuration restrictions must be enforced by the API as well
  as the UI. Never bind scheduled mail to whichever user last saved settings.
- The DMS Downloader is retired. Do not re-add its routes or navigation unless
  the user explicitly reverses that product decision.
- Maintain a professional, highly readable UI: Geist typography, mixed-case
  labels, responsive/collapsible navigation, accessible controls, temporal
  pickers for date/month/year input, and list/table presentation for mappings
  such as excluded POs. Avoid dense pill clouds and blanket uppercase styling.
- Every mapping editor and missing-mapping remediation field must expose
  searchable existing-value suggestions while still allowing a new value.
  High-growth lists (especially users, mappings, exclusions, and audit logs)
  require search plus pagination; user and audit searches run server-side.
- Pending MRN and Uninvoiced Expense PO period detection is a mandatory
  workflow gate. The UI must retain an explicit detecting/success/failure
  state and must not enable processing until detection succeeds. Processing
  APIs independently re-detect and reject empty or stale period selections.

## Required verification

Backend:

```powershell
cd backend
.\venv\Scripts\python.exe -m compileall -q app tests
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Frontend (required after any `frontend/` change):

```powershell
cd frontend
npm install
npm run build
```

The Vite build writes to `backend/app/static/`. Commit the source and generated
bundle together. Before handoff, run `git diff --check` and verify the intended
branch and remote SHA. Do not claim full desktop parity solely because the test
suite passes; the current automated parity coverage is intentionally narrower
than a complete live Oracle/SMTP/end-to-end comparison.
