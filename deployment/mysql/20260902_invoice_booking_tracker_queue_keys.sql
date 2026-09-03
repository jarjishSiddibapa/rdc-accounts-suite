-- Correct the five untouched bundled DMS queue mappings whose original
-- descriptive names did not match the live portal's actual Q keys.
--
-- Safe to run repeatedly. Each update is guarded by both the natural location
-- key and the exact old bundled values, so administrator-edited mappings are
-- never overwritten or revived.

USE rdc_accounts_suite;

UPDATE invoice_booking_tracker_mappings
SET queue_label = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_ANDHRA',
    queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_ANDHRA'
WHERE location_key = 'andhra-nellore'
  AND is_deleted = FALSE
  AND queue_label = 'Accounts payment ultrafine invoices ANDHRA/Nellore'
  AND queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_ANDHRA_NELLORE';

UPDATE invoice_booking_tracker_mappings
SET queue_label = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_FLYASH_TRADING',
    queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_FLYASH_TRADING'
WHERE location_key = 'flyash'
  AND is_deleted = FALSE
  AND queue_label = 'Accounts payment ultrafine invoices FlyAsh'
  AND queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_FLYASH';

UPDATE invoice_booking_tracker_mappings
SET queue_label = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_HEAD_OFFICE',
    queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_HEAD_OFFICE'
WHERE location_key = 'ho'
  AND is_deleted = FALSE
  AND queue_label = 'Accounts payment ultrafine invoices HO'
  AND queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_HO';

UPDATE invoice_booking_tracker_mappings
SET queue_label = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA',
    queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELENGANA'
WHERE location_key = 'telangana'
  AND is_deleted = FALSE
  AND queue_label = 'Accounts payment ultrafine invoices TELANGANA'
  AND queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_TELANGANA';

UPDATE invoice_booking_tracker_mappings
SET queue_label = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_VISAKHAPATNAM',
    queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_VISAKHAPATNAM'
WHERE location_key = 'vizag-visakhapatnam'
  AND is_deleted = FALSE
  AND queue_label = 'Accounts payment ultrafine invoices VIZAG/VISAKHAPATNAM'
  AND queue_key = 'ACCOUNTS_PAYMENT_ULTRAFINE_INVOICES_VIZAG_VISAKHAPATNAM';
