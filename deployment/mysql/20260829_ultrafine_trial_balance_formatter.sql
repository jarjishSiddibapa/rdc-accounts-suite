-- Ultrafine Trial Balance Formatter
-- Safe to run repeatedly in MySQL Workbench. No business row is deleted.
-- The 202 reference ledger classifications are inserted additively by
-- start_all.bat after this schema exists; admin edits and archives are kept.

USE `rdc_accounts_suite`;

CREATE TABLE IF NOT EXISTS `trial_balance_formatter_ledger_natures` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `ledger_key` VARCHAR(255) NOT NULL,
  `ledger_name` VARCHAR(255) NOT NULL,
  `nature` VARCHAR(2) NOT NULL DEFAULT 'Dr',
  `is_subgroup` BOOLEAN NOT NULL DEFAULT FALSE,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_trial_balance_formatter_ledger_key` (`ledger_key`),
  KEY `ix_trial_balance_formatter_ledger_key` (`ledger_key`),
  KEY `ix_trial_balance_formatter_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Handles an early development table created before subgroup treatment was
-- persisted.  MySQL versions without ADD COLUMN IF NOT EXISTS can still run
-- this migration repeatedly because the statement is selected dynamically.
SET @formatter_has_is_subgroup := (
  SELECT COUNT(*)
  FROM `information_schema`.`columns`
  WHERE `table_schema` = DATABASE()
    AND `table_name` = 'trial_balance_formatter_ledger_natures'
    AND `column_name` = 'is_subgroup'
);
SET @formatter_add_is_subgroup := IF(
  @formatter_has_is_subgroup = 0,
  'ALTER TABLE `trial_balance_formatter_ledger_natures` ADD COLUMN `is_subgroup` BOOLEAN NOT NULL DEFAULT FALSE AFTER `nature`',
  'SELECT 1'
);
PREPARE formatter_stmt FROM @formatter_add_is_subgroup;
EXECUTE formatter_stmt;
DEALLOCATE PREPARE formatter_stmt;

INSERT INTO `applications` (`key`, `label`, `company`, `is_deleted`)
VALUES (
  'trial-balance-formatter',
  'Ultrafine Trial Balance Formatter',
  'Ultrafine',
  FALSE
)
ON DUPLICATE KEY UPDATE
  `label` = VALUES(`label`),
  `company` = VALUES(`company`),
  `is_deleted` = FALSE;
