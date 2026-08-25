-- RDC Accounts Suite: durable multi-process concurrency
-- Idempotent: safe to run again on development or production.
-- If production uses a different MYSQL_DATABASE, change both occurrences
-- of `rdc_accounts_suite` below before running this file.

CREATE DATABASE IF NOT EXISTS `rdc_accounts_suite`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE `rdc_accounts_suite`;

CREATE TABLE IF NOT EXISTS `background_jobs` (
  `id` VARCHAR(36) NOT NULL,
  `owner_id` INT NOT NULL,
  `task_name` VARCHAR(255) NOT NULL,
  `args_json` LONGTEXT NOT NULL,
  `kwargs_json` LONGTEXT NOT NULL,
  `resource_key` VARCHAR(64) NULL,
  `status` VARCHAR(20) NOT NULL,
  `progress` DOUBLE NOT NULL DEFAULT 0,
  `phase` VARCHAR(255) NOT NULL DEFAULT 'Queued',
  `result_json` LONGTEXT NULL,
  `error` TEXT NULL,
  `cancel_requested` BOOLEAN NOT NULL DEFAULT FALSE,
  `priority` INT NOT NULL DEFAULT 100,
  `not_before` DATETIME NULL,
  `attempts` INT NOT NULL DEFAULT 0,
  `lease_owner` VARCHAR(128) NULL,
  `lease_expires_at` DATETIME NULL,
  `heartbeat_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `started_at` DATETIME NULL,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `finished_at` DATETIME NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  KEY `ix_background_jobs_owner_id` (`owner_id`),
  KEY `ix_background_jobs_resource_key` (`resource_key`),
  KEY `ix_background_jobs_status` (`status`),
  KEY `ix_background_jobs_priority` (`priority`),
  KEY `ix_background_jobs_not_before` (`not_before`),
  KEY `ix_background_jobs_lease_owner` (`lease_owner`),
  KEY `ix_background_jobs_lease_expires_at` (`lease_expires_at`),
  KEY `ix_background_jobs_created_at` (`created_at`),
  KEY `ix_background_jobs_updated_at` (`updated_at`),
  KEY `ix_background_jobs_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `background_job_actions` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `job_id` VARCHAR(36) NOT NULL,
  `owner_id` INT NOT NULL,
  `action` VARCHAR(100) NOT NULL,
  `status` VARCHAR(20) NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_background_job_action` (`job_id`, `action`),
  KEY `ix_background_job_actions_job_id` (`job_id`),
  KEY `ix_background_job_actions_owner_id` (`owner_id`),
  KEY `ix_background_job_actions_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_background_job_actions_job`
    FOREIGN KEY (`job_id`) REFERENCES `background_jobs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `background_resource_slots` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `resource_key` VARCHAR(64) NOT NULL,
  `slot_number` INT NOT NULL,
  `job_id` VARCHAR(36) NULL,
  `lease_owner` VARCHAR(128) NULL,
  `lease_expires_at` DATETIME NULL,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_background_resource_slot` (`resource_key`, `slot_number`),
  KEY `ix_background_resource_slots_resource_key` (`resource_key`),
  KEY `ix_background_resource_slots_job_id` (`job_id`),
  KEY `ix_background_resource_slots_lease_expires_at` (`lease_expires_at`),
  KEY `ix_background_resource_slots_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `rate_limit_buckets` (
  `limiter_key` VARCHAR(255) NOT NULL,
  `events_json` LONGTEXT NOT NULL,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`limiter_key`),
  KEY `ix_rate_limit_buckets_updated_at` (`updated_at`),
  KEY `ix_rate_limit_buckets_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `trial_balance_upload_tokens` (
  `token` VARCHAR(36) NOT NULL,
  `owner_id` INT NOT NULL,
  `input_path` TEXT NOT NULL,
  `parsed_path` TEXT NOT NULL,
  `download_filename` VARCHAR(255) NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` DATETIME NOT NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`token`),
  KEY `ix_trial_balance_upload_tokens_owner_id` (`owner_id`),
  KEY `ix_trial_balance_upload_tokens_expires_at` (`expires_at`),
  KEY `ix_trial_balance_upload_tokens_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

INSERT INTO `background_resource_slots`
  (`resource_key`, `slot_number`, `job_id`, `lease_owner`, `lease_expires_at`, `updated_at`, `is_deleted`)
VALUES
  ('oracle-gst', 1, NULL, NULL, NULL, CURRENT_TIMESTAMP, FALSE)
ON DUPLICATE KEY UPDATE
  `is_deleted` = FALSE,
  `updated_at` = CURRENT_TIMESTAMP;
