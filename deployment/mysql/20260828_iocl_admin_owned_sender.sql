-- IOCL CCMS dedicated admin-owned sender
-- Safe to run repeatedly in MySQL Workbench. No business row is deleted.

USE `rdc_accounts_suite`;

DROP PROCEDURE IF EXISTS `upgrade_iocl_admin_owned_sender`;
DELIMITER //
CREATE PROCEDURE `upgrade_iocl_admin_owned_sender`()
BEGIN
  DECLARE sender_schema_added BOOLEAN DEFAULT FALSE;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'iocl_balance_settings'
      AND COLUMN_NAME = 'sender_email'
  ) THEN
    ALTER TABLE `iocl_balance_settings`
      ADD COLUMN `sender_email` VARCHAR(255) NULL;
    SET sender_schema_added = TRUE;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'iocl_balance_settings'
      AND COLUMN_NAME = 'sender_app_password_encrypted'
  ) THEN
    ALTER TABLE `iocl_balance_settings`
      ADD COLUMN `sender_app_password_encrypted` TEXT NULL;
    SET sender_schema_added = TRUE;
  END IF;

  -- Preserve the sender used by the previous release exactly once. Prefer
  -- its selected user, then the shared system sender that was its fallback.
  -- Both source passwords are already Fernet-encrypted.
  IF sender_schema_added THEN
    UPDATE `iocl_balance_settings` s
    JOIN `email_settings` e ON e.user_id = s.sender_user_id
    SET s.sender_email = e.sender_email,
        s.sender_app_password_encrypted = e.app_password_encrypted
    WHERE s.id = 1
      AND s.sender_email IS NULL
      AND e.is_deleted = FALSE
      AND e.sender_email IS NOT NULL
      AND e.app_password_encrypted IS NOT NULL;

    UPDATE `iocl_balance_settings` s
    JOIN `system_email_settings` e ON e.id = 1
    SET s.sender_email = e.sender_email,
        s.sender_app_password_encrypted = e.app_password_encrypted
    WHERE s.id = 1
      AND s.sender_email IS NULL
      AND e.is_deleted = FALSE
      AND e.sender_email IS NOT NULL
      AND e.app_password_encrypted IS NOT NULL;
  END IF;
END//
DELIMITER ;

CALL `upgrade_iocl_admin_owned_sender`();
DROP PROCEDURE `upgrade_iocl_admin_owned_sender`;
