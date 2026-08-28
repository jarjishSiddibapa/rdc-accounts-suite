# Security notes

This is an internal application. Report suspected vulnerabilities privately to
the project administrator; do not post credentials, session files, customer
data, or production logs in a public issue.

- Keep `backend/.env`, `backend/data/`, `backend/logs/`, Oracle Instant Client,
  uploads, outputs, backups, and Fernet keys out of Git.
- Use a unique, strong initial admin password and remove the bootstrap value
  after first login.
- IOCL portal passwords, mail app passwords, and Playwright storage state are
  encrypted server-side and are never returned to the browser or serialized in
  queued jobs.
- Use HTTPS and `SESSION_COOKIE_SECURE=true` when exposing the service beyond a
  trusted LAN.
- Keep MySQL, Python dependencies, Playwright Chromium, and Oracle Client
  patched according to the organization's change process.
- Review the audit log and backups regularly. Never run ad-hoc `DELETE FROM`
  statements against business tables; use the application's soft-delete flows.
