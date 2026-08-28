# Contributing

Read `AGENTS.md` and `docs/AI_CODING_AGENT_HANDOFF.md` before editing. Keep
changes focused and preserve uncommitted work you did not create.

## Local checks

```powershell
cd backend
.\venv\Scripts\python.exe -m compileall -q app tests
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"

cd ..\frontend
npm install
npm run build
```

Frontend builds write generated assets into `backend/app/static/`; commit those
assets together with source changes. Run `git diff --check` before committing.

## Change rules

- Preserve soft deletion, owner/tab isolation, encrypted-secret boundaries, and
  shared Oracle resource slots.
- Add a dated, idempotent MySQL migration for production-visible schema/data
  changes and document the exact Workbench steps.
- Add regression coverage for behavior changes. Do not claim full desktop parity
  from unit tests alone; use representative desktop/web comparisons where the
  workflow depends on Oracle, SMTP, Playwright, or Excel-compatible workbooks.
- Do not re-add the retired DMS Downloader or mapping import/export routes
  without an explicit product decision.
