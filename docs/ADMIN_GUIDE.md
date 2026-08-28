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

Email administration controls the default sender/recipients and templates used
by the report applications. Users can edit a compose screen when the workflow
allows it, but defaults remain centralized.

## IOCL monitor

The IOCL monitor has one shared configuration owned by administrators:

- encrypted portal username/password and optional Playwright session;
- one dedicated sender email and app password;
- morning mail time, recipients, subject, and body;
- automatic-check interval;
- alert starting balance, repeat decrement, recipients, subject, and body.

Regular users can check the balance and review the complete check/notification
history, but cannot see or change credentials, recipients, templates, or rules.
Confirm the sender is ready before relying on scheduled delivery.

## Audit and maintenance

Use **Audit log** to search by user, API/action, status, or date. Use **System
maintenance** for backups and operational checks. Never delete rows directly in
MySQL; the application’s soft-delete and restore flows preserve history.
