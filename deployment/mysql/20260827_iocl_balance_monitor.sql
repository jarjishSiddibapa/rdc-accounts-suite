-- IOCL CCMS Balance Monitor
-- Safe to run repeatedly in MySQL Workbench. No business row is deleted.

CREATE DATABASE IF NOT EXISTS `rdc_accounts_suite`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE `rdc_accounts_suite`;

CREATE TABLE IF NOT EXISTS `iocl_balance_settings` (
  `id` INT NOT NULL,
  `enabled` BOOLEAN NOT NULL DEFAULT FALSE,
  `login_url` VARCHAR(500) NOT NULL,
  `username` VARCHAR(255) NULL,
  `password_encrypted` TEXT NULL,
  `session_state_encrypted` LONGTEXT NULL,
  `login_timeout_seconds` INT NOT NULL DEFAULT 60,
  `check_interval_minutes` INT NOT NULL DEFAULT 30,
  `next_check_at` DATETIME NULL,
  `check_lock_token` VARCHAR(36) NULL,
  `check_lock_expires_at` DATETIME NULL,
  `sender_user_id` INT NULL,
  `sender_email` VARCHAR(255) NULL,
  `sender_app_password_encrypted` TEXT NULL,
  `daily_email_enabled` BOOLEAN NOT NULL DEFAULT TRUE,
  `daily_email_time` VARCHAR(5) NOT NULL DEFAULT '08:00',
  `daily_to` TEXT NULL,
  `daily_cc` TEXT NULL,
  `daily_subject_template` TEXT NOT NULL,
  `daily_body_template` LONGTEXT NOT NULL,
  `last_daily_sent_date` DATE NULL,
  `last_daily_attempt_at` DATETIME NULL,
  `alerts_enabled` BOOLEAN NOT NULL DEFAULT TRUE,
  `alert_start_amount` DECIMAL(14,2) NOT NULL DEFAULT 500000.00,
  `alert_step_amount` DECIMAL(14,2) NOT NULL DEFAULT 50000.00,
  `alert_to` TEXT NULL,
  `alert_cc` TEXT NULL,
  `alert_subject_template` TEXT NOT NULL,
  `alert_body_template` LONGTEXT NOT NULL,
  `last_balance` DECIMAL(14,2) NULL,
  `last_checked_at` DATETIME NULL,
  `last_check_status` VARCHAR(20) NULL,
  `last_error` TEXT NULL,
  `updated_at` DATETIME NOT NULL,
  `version` INT NOT NULL DEFAULT 1,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  INDEX `ix_iocl_balance_settings_next_check_at` (`next_check_at`),
  INDEX `ix_iocl_balance_settings_check_lock_expires_at` (`check_lock_expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

DROP PROCEDURE IF EXISTS `upgrade_iocl_balance_settings`;
DELIMITER //
CREATE PROCEDURE `upgrade_iocl_balance_settings`()
BEGIN
  DECLARE sender_schema_added BOOLEAN DEFAULT FALSE;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'iocl_balance_settings'
      AND COLUMN_NAME = 'last_daily_attempt_at'
  ) THEN
    ALTER TABLE `iocl_balance_settings`
      ADD COLUMN `last_daily_attempt_at` DATETIME NULL;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'iocl_balance_settings'
      AND COLUMN_NAME = 'sender_user_id'
  ) THEN
    ALTER TABLE `iocl_balance_settings`
      ADD COLUMN `sender_user_id` INT NULL;
  END IF;
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
CALL `upgrade_iocl_balance_settings`();
DROP PROCEDURE `upgrade_iocl_balance_settings`;

CREATE TABLE IF NOT EXISTS `iocl_balance_checks` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `trigger` VARCHAR(20) NOT NULL,
  `status` VARCHAR(20) NOT NULL,
  `balance` DECIMAL(14,2) NULL,
  `error_message` TEXT NULL,
  `checked_at` DATETIME NOT NULL,
  `duration_seconds` FLOAT NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  INDEX `ix_iocl_balance_checks_checked_at` (`checked_at`),
  INDEX `ix_iocl_balance_checks_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `iocl_balance_notifications` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `notification_key` VARCHAR(160) NOT NULL,
  `check_id` BIGINT NULL,
  `notification_type` VARCHAR(20) NOT NULL,
  `threshold_amount` DECIMAL(14,2) NULL,
  `balance` DECIMAL(14,2) NOT NULL,
  `subject` TEXT NOT NULL,
  `body` LONGTEXT NOT NULL,
  `to_recipients` TEXT NOT NULL,
  `cc_recipients` TEXT NULL,
  `status` VARCHAR(20) NOT NULL,
  `error_message` TEXT NULL,
  `created_at` DATETIME NOT NULL,
  `attempted_at` DATETIME NULL,
  `sent_at` DATETIME NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_iocl_balance_notification_key` (`notification_key`),
  INDEX `ix_iocl_balance_notifications_check_id` (`check_id`),
  INDEX `ix_iocl_balance_notifications_notification_type` (`notification_type`),
  INDEX `ix_iocl_balance_notifications_status` (`status`),
  INDEX `ix_iocl_balance_notifications_created_at` (`created_at`),
  INDEX `ix_iocl_balance_notifications_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_iocl_balance_notifications_check`
    FOREIGN KEY (`check_id`) REFERENCES `iocl_balance_checks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

INSERT INTO `iocl_balance_settings` (
  `id`, `enabled`, `login_url`, `username`, `password_encrypted`,
  `session_state_encrypted`, `login_timeout_seconds`, `check_interval_minutes`,
  `daily_email_enabled`, `daily_email_time`, `daily_to`, `daily_cc`,
  `daily_subject_template`, `daily_body_template`, `alerts_enabled`,
  `alert_start_amount`, `alert_step_amount`, `alert_to`, `alert_cc`,
  `alert_subject_template`, `alert_body_template`, `updated_at`, `version`, `is_deleted`
) VALUES (
  1, FALSE, 'https://beta.iocxtrapower.com/account/login?returnUrl=%2F', NULL, NULL,
  NULL, 60, 30, TRUE, '08:00', '[]', '[]',
  'IOCL Balance as on {date}',
  'Dear Team,\n\nIOCL Balance as on {date} is {balance}.\n\nThanks,\nUltrafine Team',
  TRUE, 500000.00, 50000.00, '[]', '[]',
  'Alert - IOCL CCMS balance is below {threshold}.',
  'Dear Team,\n\nThis is a reminder mail.\n\nCCMS balance of IOCL has reached below {threshold}, Please recharge in priority.\nAvailable CCMS balance - {balance}\n',
  UTC_TIMESTAMP(), 1, FALSE
) ON DUPLICATE KEY UPDATE `is_deleted` = FALSE;

INSERT INTO `applications` (`key`, `label`, `company`, `is_deleted`)
VALUES ('iocl-balance-monitor', 'Ultrafine IOCL Balance Monitor', 'Ultrafine', FALSE)
ON DUPLICATE KEY UPDATE
  `label` = 'Ultrafine IOCL Balance Monitor',
  `is_deleted` = FALSE;
