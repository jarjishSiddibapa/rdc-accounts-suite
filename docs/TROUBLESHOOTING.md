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

## Startup says the supervisor is already running

Update to the launcher with verified previous-run cleanup and run
`start_all.bat` again after confirming no important job is active. It requests
a graceful stop, clears only this repository's suite processes, and waits for
port `2805`. If it reports an unrelated port owner, inspect that PID rather
than killing it blindly. The historical combination of “supervisor is already
running” followed by exit code 2 meant the old launcher had continued before a
suppressed `taskkill` failure or incomplete process-tree shutdown released the
supervisor lock; it was not database corruption.

## Oracle report fails

Confirm Oracle host/service/user values and Instant Client installation. Do not
raise `ORACLE_GST_JOB_CONCURRENCY` without DBA approval. Review the worker log
for the timestamp, not for credentials.

## IOCL balance check fails

An administrator should verify the encrypted portal credentials, session import,
timeout, and network access to the portal. CAPTCHA/session expiry may require a
new sanitized-by-policy Playwright storage state. Check the complete history for
the error and retry manually.

If older logs say `get_by_text("Financials").first` timed out on a hidden span,
update to the release containing stable saved-session validation and visible-nav
selection. That error commonly means an expired session briefly left the login
URL and then returned; it does not by itself mean the Financials feature or the
stored credentials are unavailable.

## Mail is not delivered

Verify the administrator-owned sender is ready, app-password authentication is
accepted by the provider, and To/Cc addresses are valid. Use the in-app test
mail only after confirming recipients. The notification history records pending,
sending, sent, and failed states.

## Invoice Booking Tracker check fails

Administrators should open the failed check row for the real technical detail.
Verify DMS credentials/session, queue label/key, portal availability, and the
configured timeout. A repeated-page or “scanned X of Y” error is deliberately
fatal: it means pagination could not prove that every row was counted. Correct
the queue mapping or portal issue and run a manual check; do not bypass this
guard or send a partial tracker.

If the administrator page says **Automation off**, enable **Enable daily
tracker automation** and save. The separate **Send the scheduled tracker mail**
checkbox and a visible 08:00 time do not override the master switch. For an
older installation whose untouched seeded Andhra, FlyAsh, HO, Telangana, or
Vizag queue fails, run
`deployment/mysql/20260902_invoice_booking_tracker_queue_keys.sql` and restart
the suite; the script preserves any administrator-edited mapping.

“The DMS account is already logged in” is an expected portal state, not a
server fault. Ask the current DMS user to sign out, then retry. The automated
08:00 run performs a best-effort explicit DMS logout after every browser run so
it does not keep the single-login account occupied after processing.

“A tracker check is already running” is a *different* condition from the one
above - it is this application's own DB concurrency lock
(`check_lock_token`/`check_lock_expires_at`), unrelated to DMS session state.
It self-renews in short windows while a real scan is active and self-expires
within a few minutes of the last renewal if the worker process died outright
(crash, OOM, service restart) mid-scan. If it persists for longer than that
with no Chromium/Python process actually running, it is a genuinely orphaned
lock; confirm with
`SELECT check_lock_token, check_lock_expires_at, NOW() FROM invoice_booking_tracker_settings WHERE id = 1;`
and, only once nothing is actually still scanning, clear it with
`UPDATE invoice_booking_tracker_settings SET check_lock_token = NULL, check_lock_expires_at = NULL WHERE id = 1;`.

## API requests time out with `QueuePool limit`

Use only `start_all.bat`, confirm the role-specific pool settings have not been
overridden to smaller values, and check that `audit_middleware.py` still defers
the MySQL audit insert until after the response. The audit JSON-lines file is
written immediately, but performing the database mirror synchronously can ask
for a second connection while the endpoint still holds its first and cause a
burst-time deadlock. Do not solve this only by raising the pool without first
restoring the deferred audit behavior.

## Frontend changes are not visible

Run `npm install` and `npm run build` from `frontend/`, confirm generated files
under `backend/app/static/` are committed, then restart `start_all.bat` and hard
refresh the browser.
