-- Ultrafine Invoice Booking Tracker
-- Idempotent: safe to run repeatedly in MySQL Workbench. No row is deleted.

CREATE DATABASE IF NOT EXISTS `rdc_accounts_suite`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE `rdc_accounts_suite`;

CREATE TABLE IF NOT EXISTS `invoice_booking_tracker_settings` (
  `id` INT NOT NULL,
  `enabled` BOOLEAN NOT NULL DEFAULT FALSE,
  `login_url` VARCHAR(500) NOT NULL DEFAULT 'https://dms.rdc.in/',
  `username` VARCHAR(255) NULL,
  `password_encrypted` TEXT NULL,
  `session_state_encrypted` LONGTEXT NULL,
  `login_timeout_seconds` INT NOT NULL DEFAULT 90,
  `sender_email` VARCHAR(255) NULL,
  `sender_app_password_encrypted` TEXT NULL,
  `scheduled_email_enabled` BOOLEAN NOT NULL DEFAULT TRUE,
  `scheduled_email_time` VARCHAR(5) NOT NULL DEFAULT '08:00',
  `mail_to` TEXT NULL,
  `mail_cc` TEXT NULL,
  `subject_template` TEXT NOT NULL,
  `body_template` LONGTEXT NOT NULL,
  `last_scheduled_sent_date` DATE NULL,
  `last_scheduled_attempt_date` DATE NULL,
  `check_lock_token` VARCHAR(36) NULL,
  `check_lock_expires_at` DATETIME NULL,
  `last_total_pending` INT NULL,
  `last_checked_at` DATETIME NULL,
  `last_check_status` VARCHAR(20) NULL,
  `last_error` TEXT NULL,
  `updated_at` DATETIME NOT NULL,
  `version` INT NOT NULL DEFAULT 1,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  INDEX `ix_invoice_booking_tracker_settings_lock` (`check_lock_expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `invoice_booking_tracker_mappings` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `location_key` VARCHAR(255) NOT NULL,
  `location` VARCHAR(255) NOT NULL,
  `responsible_person` VARCHAR(255) NOT NULL,
  `queue_label` VARCHAR(500) NOT NULL,
  `queue_key` VARCHAR(500) NULL,
  `sort_order` INT NOT NULL DEFAULT 0,
  `is_active` BOOLEAN NOT NULL DEFAULT TRUE,
  `updated_at` DATETIME NOT NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_invoice_booking_tracker_location_key` (`location_key`),
  INDEX `ix_invoice_booking_tracker_mappings_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `invoice_booking_tracker_checks` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `trigger` VARCHAR(20) NOT NULL,
  `status` VARCHAR(20) NOT NULL,
  `total_pending` INT NULL,
  `total_records_scanned` INT NULL,
  `total_pages_scanned` INT NULL,
  `result_json` LONGTEXT NULL,
  `error_message` TEXT NULL,
  `checked_at` DATETIME NOT NULL,
  `duration_seconds` FLOAT NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  INDEX `ix_invoice_booking_tracker_checks_trigger` (`trigger`),
  INDEX `ix_invoice_booking_tracker_checks_status` (`status`),
  INDEX `ix_invoice_booking_tracker_checks_checked_at` (`checked_at`),
  INDEX `ix_invoice_booking_tracker_checks_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `invoice_booking_tracker_notifications` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `notification_key` VARCHAR(160) NOT NULL,
  `check_id` BIGINT NULL,
  `subject` TEXT NOT NULL,
  `body` LONGTEXT NOT NULL,
  `to_recipients` TEXT NOT NULL,
  `cc_recipients` TEXT NULL,
  `result_json` LONGTEXT NOT NULL,
  `attachment_filename` VARCHAR(500) NOT NULL,
  `status` VARCHAR(20) NOT NULL,
  `error_message` TEXT NULL,
  `created_at` DATETIME NOT NULL,
  `attempted_at` DATETIME NULL,
  `sent_at` DATETIME NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_invoice_booking_tracker_notification_key` (`notification_key`),
  INDEX `ix_invoice_booking_tracker_notifications_check` (`check_id`),
  INDEX `ix_invoice_booking_tracker_notifications_status` (`status`),
  INDEX `ix_invoice_booking_tracker_notifications_created` (`created_at`),
  INDEX `ix_invoice_booking_tracker_notifications_deleted` (`is_deleted`),
  CONSTRAINT `fk_invoice_booking_tracker_notification_check`
    FOREIGN KEY (`check_id`) REFERENCES `invoice_booking_tracker_checks` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

INSERT INTO `invoice_booking_tracker_settings` (
  `id`, `enabled`, `login_url`, `login_timeout_seconds`,
  `scheduled_email_enabled`, `scheduled_email_time`, `mail_to`, `mail_cc`,
  `subject_template`, `body_template`, `updated_at`, `version`, `is_deleted`
) VALUES (
  1, FALSE, 'https://dms.rdc.in/', 90,
  TRUE, '08:00', '[]', '[]',
  'Ultrafine Pending Invoice Booking Tracker as on {date}',
  'Dear Team,\n\nPlease find below the Ultrafine pending invoice booking tracker as on {date}.\n\n{tracker_table}\n\nTotal pending invoices: {total_pending}\n\nThanks,\nUltrafine Team',
  UTC_TIMESTAMP(), 1, FALSE
) ON DUPLICATE KEY UPDATE `id` = `id`;

INSERT IGNORE INTO `invoice_booking_tracker_mappings`
  (`location_key`, `location`, `responsible_person`, `queue_label`, `queue_key`, `sort_order`, `is_active`, `updated_at`, `is_deleted`)
VALUES
  ('capex', 'CAPEX', 'Khushi', 'Accounts payment ultrafine CAPEX invoices', 'ACCOUNTS_PAYMENT_ULTRAFINE_CAPEX_INVOICES', 1, TRUE, UTC_TIMESTAMP(), FALSE),
  ('andhra-nellore', 'ANDHRA/Nellore', 'Jaysukh', 'Accounts payment ultrafine invoices ANDHRA/Nellore', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_ANDHRA_NELLORE', 2, TRUE, UTC_TIMESTAMP(), FALSE),
  ('bangalore', 'BANGALORE', 'Hitanshi', 'Accounts payment ultrafine invoices BANGALORE', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_BANGALORE', 3, TRUE, UTC_TIMESTAMP(), FALSE),
  ('flyash', 'FlyAsh', 'Hitanshi', 'Accounts payment ultrafine invoices FlyAsh', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_FLYASH', 4, TRUE, UTC_TIMESTAMP(), FALSE),
  ('goa', 'GOA', 'Rakesh', 'Accounts payment ultrafine invoices GOA', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_GOA', 5, TRUE, UTC_TIMESTAMP(), FALSE),
  ('ho', 'HO', 'Hitanshi', 'Accounts payment ultrafine invoices HO', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_HO', 6, TRUE, UTC_TIMESTAMP(), FALSE),
  ('nagpur', 'NAGPUR', 'Vishal', 'Accounts payment ultrafine invoices NAGPUR', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_NAGPUR', 7, TRUE, UTC_TIMESTAMP(), FALSE),
  ('odisha', 'ODISHA', 'Hitanshi', 'Accounts payment ultrafine invoices ODISHA', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_ODISHA', 8, TRUE, UTC_TIMESTAMP(), FALSE),
  ('raipur', 'RAIPUR', 'Hitanshi', 'Accounts payment ultrafine invoices RAIPUR', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_RAIPUR', 9, TRUE, UTC_TIMESTAMP(), FALSE),
  ('surat', 'SURAT', 'Jaysukh', 'Accounts payment ultrafine invoices SURAT', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_SURAT', 10, TRUE, UTC_TIMESTAMP(), FALSE),
  ('tamil-nadu', 'TAMIL NADU', 'Hitanshi', 'Accounts payment ultrafine invoices TAMILNADU', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TAMILNADU', 11, TRUE, UTC_TIMESTAMP(), FALSE),
  ('telangana', 'TELANGANA', 'Hitanshi', 'Accounts payment ultrafine invoices TELANGANA', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELANGANA', 12, TRUE, UTC_TIMESTAMP(), FALSE),
  ('vizag-visakhapatnam', 'VIZAG/VISAKHAPATNAM', 'Jaysukh', 'Accounts payment ultrafine invoices VIZAG/VISAKHAPATNAM', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_VIZAG_VISAKHAPATNAM', 13, TRUE, UTC_TIMESTAMP(), FALSE),
  ('wada', 'WADA', 'Vishal', 'Accounts payment ultrafine invoices WADA', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_WADA', 14, TRUE, UTC_TIMESTAMP(), FALSE),
  ('west-bengal-aggregate-microsilica-howrah', 'West Bengal (Aggregate, Microsilica & Howrah)', 'Ashutosh', 'Accounts payment ultrafine invoices West Bengal (Aggregate, Microsilica & Howrah)', 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_WEST_BENGAL', 15, TRUE, UTC_TIMESTAMP(), FALSE);

INSERT INTO `applications` (`key`, `label`, `company`, `is_deleted`)
VALUES ('invoice-booking-tracker', 'Ultrafine Invoice Booking Tracker', 'Ultrafine', FALSE)
ON DUPLICATE KEY UPDATE
  `label` = VALUES(`label`),
  `company` = VALUES(`company`),
  `is_deleted` = FALSE;
