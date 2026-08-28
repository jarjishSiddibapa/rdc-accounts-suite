# Troubleshooting

## Login times out

Check that MySQL is reachable, `backend/.env` has the correct database values,
and the supervisor is running. Inspect `backend/logs/` and
`http://127.0.0.1:2805/api/health`. A restart is safe after confirming no
important job is still running.

## A report is stuck or disappears

Jobs are durable and owner/tab scoped. Keep the originating tab open while a
cancellable job runs. If a browser crashed, the client lease expires and the
worker releases resources; start the report again after the stale job settles.

## Oracle report fails

Confirm Oracle host/service/user values and Instant Client installation. Do not
raise `ORACLE_GST_JOB_CONCURRENCY` without DBA approval. Review the worker log
for the timestamp, not for credentials.

## IOCL balance check fails

An administrator should verify the encrypted portal credentials, session import,
timeout, and network access to the portal. CAPTCHA/session expiry may require a
new sanitized-by-policy Playwright storage state. Check the complete history for
the error and retry manually.

## Mail is not delivered

Verify the administrator-owned sender is ready, app-password authentication is
accepted by the provider, and To/Cc addresses are valid. Use the in-app test
mail only after confirming recipients. The notification history records pending,
sending, sent, and failed states.

## Frontend changes are not visible

Run `npm install` and `npm run build` from `frontend/`, confirm generated files
under `backend/app/static/` are committed, then restart `start_all.bat` and hard
refresh the browser.
