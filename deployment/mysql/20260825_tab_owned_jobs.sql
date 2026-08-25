-- RDC Accounts Suite: browser-tab-owned background job leases
-- Idempotent: safe to paste into MySQL Workbench more than once.
-- If production uses a different MYSQL_DATABASE, change both occurrences
-- of `rdc_accounts_suite` below before running this script.

CREATE DATABASE IF NOT EXISTS `rdc_accounts_suite`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE `rdc_accounts_suite`;

DROP PROCEDURE IF EXISTS `add_tab_owned_job_columns`;
DELIMITER //
CREATE PROCEDURE `add_tab_owned_job_columns`()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'background_jobs'
      AND COLUMN_NAME = 'client_tab_id'
  ) THEN
    ALTER TABLE `background_jobs`
      ADD COLUMN `client_tab_id` VARCHAR(64) NULL,
      ADD INDEX `ix_background_jobs_client_tab_id` (`client_tab_id`);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'background_jobs'
      AND COLUMN_NAME = 'client_heartbeat_at'
  ) THEN
    ALTER TABLE `background_jobs`
      ADD COLUMN `client_heartbeat_at` DATETIME NULL,
      ADD INDEX `ix_background_jobs_client_heartbeat_at` (`client_heartbeat_at`);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'background_jobs'
      AND COLUMN_NAME = 'cancel_on_disconnect'
  ) THEN
    ALTER TABLE `background_jobs`
      ADD COLUMN `cancel_on_disconnect` BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;
END//
DELIMITER ;

CALL `add_tab_owned_job_columns`();
DROP PROCEDURE `add_tab_owned_job_columns`;
