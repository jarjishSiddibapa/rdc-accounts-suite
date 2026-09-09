-- IT POs Lookup: PO -> ERP payment document number -> bank statement UTR
-- Safe to run repeatedly in MySQL Workbench. No business row is deleted.

USE `rdc_accounts_suite`;

DROP PROCEDURE IF EXISTS `add_po_lookup_role_column`;
DELIMITER //
CREATE PROCEDURE `add_po_lookup_role_column`()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'po_lookup_role'
  ) THEN
    ALTER TABLE `users`
      ADD COLUMN `po_lookup_role` VARCHAR(16) NULL;
  END IF;
END//
DELIMITER ;
CALL `add_po_lookup_role_column`();
DROP PROCEDURE `add_po_lookup_role_column`;

CREATE TABLE IF NOT EXISTS `po_lookup_statement_uploads` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `filename` VARCHAR(255) NOT NULL,
  `account_number` VARCHAR(64) NULL,
  `from_date` DATE NULL,
  `to_date` DATE NULL,
  `row_count` INT NOT NULL,
  `inserted_count` INT NOT NULL,
  `duplicate_count` INT NOT NULL,
  `uploaded_by_id` INT NOT NULL,
  `uploaded_at` DATETIME NOT NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  INDEX `ix_po_lookup_statement_uploads_uploaded_at` (`uploaded_at`),
  INDEX `ix_po_lookup_statement_uploads_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_po_lookup_upload_user`
    FOREIGN KEY (`uploaded_by_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS `po_lookup_bank_transactions` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `account_number` VARCHAR(64) NOT NULL,
  `transaction_date` DATETIME NOT NULL,
  `transaction_description` TEXT NOT NULL,
  `transaction_amount` DECIMAL(16,2) NOT NULL,
  `debit_credit` VARCHAR(1) NULL,
  `reference_no` VARCHAR(64) NULL,
  `value_date` DATE NULL,
  `transaction_branch` VARCHAR(128) NULL,
  `running_balance` DECIMAL(16,2) NULL,
  `upload_id` BIGINT NULL,
  `is_deleted` BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_po_lookup_transaction` (`account_number`, `transaction_date`, `reference_no`, `transaction_amount`),
  INDEX `ix_po_lookup_bank_transactions_account_number` (`account_number`),
  INDEX `ix_po_lookup_bank_transactions_reference_no` (`reference_no`),
  INDEX `ix_po_lookup_bank_transactions_upload_id` (`upload_id`),
  INDEX `ix_po_lookup_bank_transactions_is_deleted` (`is_deleted`),
  CONSTRAINT `fk_po_lookup_transaction_upload`
    FOREIGN KEY (`upload_id`) REFERENCES `po_lookup_statement_uploads` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

INSERT INTO `applications` (`key`, `label`, `company`, `is_deleted`)
VALUES ('it-po-lookup', 'IT POs Lookup', 'RDC', FALSE)
ON DUPLICATE KEY UPDATE
  `label` = 'IT POs Lookup',
  `is_deleted` = FALSE;
