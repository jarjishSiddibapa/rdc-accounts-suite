-- IOCL recurring below-threshold reminder interval
-- Safe to run repeatedly in MySQL Workbench. No row or legacy column is deleted.

USE `rdc_accounts_suite`;

DROP PROCEDURE IF EXISTS `upgrade_iocl_recurring_threshold_reminders`;
DELIMITER //
CREATE PROCEDURE `upgrade_iocl_recurring_threshold_reminders`()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'iocl_balance_settings'
      AND COLUMN_NAME = 'alert_repeat_hours'
  ) THEN
    ALTER TABLE `iocl_balance_settings`
      ADD COLUMN `alert_repeat_hours` INT NOT NULL DEFAULT 30;
  END IF;
END//
DELIMITER ;

CALL `upgrade_iocl_recurring_threshold_reminders`();
DROP PROCEDURE `upgrade_iocl_recurring_threshold_reminders`;
