-- IT POs Lookup was retired from the application on 2026-09-18 (application
-- code removed; see permissions.py's RETIRED_APP_KEYS). This migration is
-- OPTIONAL and NOT applied automatically - it permanently deletes every
-- uploaded bank statement and parsed transaction line this feature ever
-- stored. Only run this once you've confirmed that audit trail is no longer
-- needed for anything (reconciliation history, disputes, etc).
--
-- Safe to run repeatedly in MySQL Workbench.

USE `rdc_accounts_suite`;

-- Mark the catalogue entry retired (the application code no longer serves
-- it, so this just keeps the applications table's own bookkeeping in sync -
-- app/permissions.py's seed_applications() also does this automatically on
-- every startup, so this line is redundant with a running app but harmless).
UPDATE `applications` SET `is_deleted` = TRUE WHERE `key` = 'it-po-lookup';

-- Uncomment the three statements below to actually drop the feature's data
-- and the now-unused per-user role column. Left commented out on purpose.

-- DROP TABLE IF EXISTS `po_lookup_bank_transactions`;
-- DROP TABLE IF EXISTS `po_lookup_statement_uploads`;
-- ALTER TABLE `users` DROP COLUMN `po_lookup_role`;
