-- Ultrafine Creditors Ageing Report Generator
-- Safe to run repeatedly in MySQL Workbench. No business row is deleted.
-- The 208 packaged vendor mappings are inserted additively by start_all.bat
-- after this schema exists; existing and archived rows are never overwritten.

USE `rdc_accounts_suite`;

CREATE TABLE IF NOT EXISTS `creditors_ageing_vendor_mappings` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `vendor_key` VARCHAR(255) NOT NULL,
  `vendor_name` VARCHAR(255) NOT NULL,
  `location` VARCHAR(255) NOT NULL DEFAULT '',
  `vendor_type` VARCHAR(255) NOT NULL DEFAULT '',
  `vendor_sub_type` VARCHAR(255) NOT NULL DEFAULT '',
  `intercompany` BOOLEAN NOT NULL DEFAULT FALSE,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_creditors_ageing_vendor_key` (`vendor_key`),
  KEY `ix_creditors_ageing_vendor_key` (`vendor_key`),
  KEY `ix_creditors_ageing_is_deleted` (`is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO `applications` (`key`, `label`, `company`, `is_deleted`)
VALUES (
  'creditors-ageing-report',
  'Ultrafine Creditors Ageing Report Generator',
  'Ultrafine',
  FALSE
)
ON DUPLICATE KEY UPDATE
  `label` = VALUES(`label`),
  `is_deleted` = FALSE;

