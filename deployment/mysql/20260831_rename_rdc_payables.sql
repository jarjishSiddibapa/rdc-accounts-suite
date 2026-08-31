-- Rename the existing application catalogue entry without changing its stable
-- permission key, routes, user grants, mappings, or report data.
-- Safe to run repeatedly; no schema changes and no rows are deleted.

USE `rdc_accounts_suite`;

UPDATE `applications`
SET `label` = 'Loans & Advance, IOCL, TDS Report Generator'
WHERE `key` = 'rdc-payables'
  AND `is_deleted` = FALSE;

SELECT `key`, `label`, `company`, `is_deleted`
FROM `applications`
WHERE `key` = 'rdc-payables';
