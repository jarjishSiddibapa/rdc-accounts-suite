-- Correct IOCL below-threshold reminder cadence from hours to minutes.
-- Safe to run repeatedly in MySQL Workbench. Nothing is deleted.
-- The old numeric value is copied unchanged because administrators entered
-- 30 intending a 30-minute reminder.

USE `rdc_accounts_suite`;

DROP PROCEDURE IF EXISTS `upgrade_iocl_reminder_minutes`;
DELIMITER //
CREATE PROCEDURE `upgrade_iocl_reminder_minutes`()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'iocl_balance_settings'
      AND COLUMN_NAME = 'alert_repeat_minutes'
  ) THEN
    ALTER TABLE `iocl_balance_settings`
      ADD COLUMN `alert_repeat_minutes` INT NOT NULL DEFAULT 30;

    IF EXISTS (
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'iocl_balance_settings'
        AND COLUMN_NAME = 'alert_repeat_hours'
    ) THEN
      UPDATE `iocl_balance_settings`
      SET `alert_repeat_minutes` = GREATEST(
        1,
        LEAST(43200, COALESCE(`alert_repeat_hours`, 30))
      );
    END IF;
  END IF;
END//
DELIMITER ;

CALL `upgrade_iocl_reminder_minutes`();
DROP PROCEDURE `upgrade_iocl_reminder_minutes`;

SELECT `id`, `check_interval_minutes`, `alert_repeat_minutes`
FROM `iocl_balance_settings`;
