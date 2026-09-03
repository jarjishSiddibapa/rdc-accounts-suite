-- Add an admin-configurable mail signature to the Invoice Booking Tracker,
-- and refresh the bundled default mail body to the proven wording. Both
-- changes are safe to run repeatedly in MySQL Workbench.

USE `rdc_accounts_suite`;

DROP PROCEDURE IF EXISTS `add_invoice_booking_tracker_signature`;
DELIMITER //
CREATE PROCEDURE `add_invoice_booking_tracker_signature`()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'invoice_booking_tracker_settings'
      AND COLUMN_NAME = 'signature'
  ) THEN
    ALTER TABLE `invoice_booking_tracker_settings`
      ADD COLUMN `signature` TEXT NULL;
  END IF;
END//
DELIMITER ;
CALL `add_invoice_booking_tracker_signature`();
DROP PROCEDURE `add_invoice_booking_tracker_signature`;

-- Only replace the body template if it still holds the original bundled
-- default word-for-word - an administrator's own edited wording always wins.
UPDATE `invoice_booking_tracker_settings`
SET `body_template` = 'Dear All,\n\nKindly proceed with booking of the pending invoices listed below. If any of these have already been booked, please update their status in the DMS system accordingly.\n\n{tracker_table}'
WHERE `id` = 1
  AND `body_template` = 'Dear Team,\n\nPlease find below the Ultrafine pending invoice booking tracker as on {date}.\n\n{tracker_table}\n\nTotal pending invoices: {total_pending}\n\nThanks,\nUltrafine Team';
