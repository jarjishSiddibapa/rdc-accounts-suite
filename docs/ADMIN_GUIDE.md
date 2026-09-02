# Administrator guide

## Users and access

Open **Users** to create accounts, optionally provide first/last names, assign
the administrator or user role, activate/deactivate accounts, archive users,
reset passwords, and grant application access. Email is required and unique.

Use the search, role/status filters, and pagination for larger directories.

## Central mappings and defaults

Mapping pages are the central source of truth. Existing values appear as
searchable suggestions while typing, but a new value can still be entered when
appropriate. Exclusions and other growing lists use tables with search and
pagination. Mapping workbook import/export is intentionally retired.

The Ultrafine Creditors Ageing tool starts with all 208 mappings from the
reference desktop application. Its Vendor mappings tab supports search,
type-ahead reuse of existing classification values, additive entries, edits,
soft archive, and restore. Startup seeds only natural keys that have never
existed, so administrator changes always take precedence over packaged data.

The Ultrafine Trial Balance Formatter starts with the 202 ledger natures and
six subgroup-total decisions verified against the supplied finished report.
Its searchable Ledger mappings tab supports add, edit, soft archive, and
restore. Startup adds only missing normalized ledger keys; it does not overwrite
an administrator's classification or implicitly revive an archived row.

Email administration controls the default sender/recipients and templates used
by the report applications. Users can edit a compose screen when the workflow
allows it, but defaults remain centralized.

The catalogue label **Loans & Advance, IOCL, TDS Report Generator** uses the
stable internal key `rdc-payables`. Do not rename the key or route: existing
application grants and saved links depend on it. The display-name migration is
label-only and does not alter mappings or permissions.

## IOCL monitor

The IOCL monitor has one shared configuration owned by administrators:

- encrypted portal username/password and optional Playwright session;
- one dedicated sender email and app password;
- morning mail time, recipients, subject, and body;
- automatic-check interval;
- alert starting balance, repeat interval in minutes, recipients, subject, and body.

Regular users can check the balance and review the complete check/notification
history, but cannot see or change credentials, recipients, templates, or rules.
Confirm the sender is ready before relying on scheduled delivery.

## Invoice Booking Tracker

The tracker has one shared administrator-owned configuration: encrypted DMS
credentials and optional browser session, one dedicated sender, exact IST send
time, To/Cc, subject/body templates, and centralized location-to-work-queue
mappings. The body supports `{date}`, `{total_pending}`, `{location_count}`, and
`{tracker_table}`; the subject supports every placeholder except the HTML table.

Mappings are seeded additively from `hitanshi.docx`. Search and edit them in the
tracker page; pause a queue with **Active**, or archive/restore it. Never edit
these tables directly or reintroduce mapping import/export. Before enabling the
schedule, use **Check tracker now** and confirm the per-location record/page
counts, then send the test mail only to your own administrator address.

The DMS portal permits only one active session for this ID. The scheduled run
explicitly logs out after its complete scan so it does not block staff arriving
later. A manual run made while a person is already signed in records the check
as failed and shows an “account already logged in” state; it never attempts to
force that person out.

## Audit and maintenance

Use **Audit log** to search by user, API/action, status, or date. Use **System
maintenance** for backups and operational checks. Never delete rows directly in
MySQL; the application’s soft-delete and restore flows preserve history.
