-- RDC Accounts Suite: referential-integrity hardening
--
-- These 5 columns were always conceptually foreign keys (app_key -> a real
-- Application, owner_id -> a real User, job_id -> a real BackgroundJob) but
-- were never declared as such at the database level - the application layer
-- already validated them before every write, so this closes a defense-in-
-- depth gap rather than fixing an observed bug.
--
-- Safe to run repeatedly (each ALTER is guarded by an information_schema
-- check) and safe to run on a live production database with existing data:
-- before adding each constraint, this checks for rows that would violate it
-- and SKIPS that one constraint (printing a warning row instead of failing)
-- rather than erroring out or deleting/modifying any business data. If a
-- warning appears, the underlying orphaned rows need to be looked at by hand
-- before that particular constraint can be added on a later run.
--
-- No table in this suite is ever hard-deleted (see app/soft_delete.py - every
-- table carries is_deleted instead), so under normal operation none of these
-- checks should find orphans.

USE `rdc_accounts_suite`;

DROP PROCEDURE IF EXISTS `add_missing_foreign_keys`;
DELIMITER //
CREATE PROCEDURE `add_missing_foreign_keys`()
BEGIN
  -- 1. application_email_recipients.app_key -> applications.key
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'application_email_recipients'
      AND CONSTRAINT_NAME = 'fk_application_email_recipients_app'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM `application_email_recipients` r
      LEFT JOIN `applications` a ON a.`key` = r.`app_key`
      WHERE a.`key` IS NULL
    ) THEN
      SELECT 'SKIPPED fk_application_email_recipients_app: orphaned app_key value(s) found - fix the data first' AS warning;
    ELSE
      ALTER TABLE `application_email_recipients`
        ADD CONSTRAINT `fk_application_email_recipients_app`
        FOREIGN KEY (`app_key`) REFERENCES `applications` (`key`);
    END IF;
  END IF;

  -- 2. background_jobs.owner_id -> users.id
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'background_jobs'
      AND CONSTRAINT_NAME = 'fk_background_jobs_owner'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM `background_jobs` j
      LEFT JOIN `users` u ON u.`id` = j.`owner_id`
      WHERE u.`id` IS NULL
    ) THEN
      SELECT 'SKIPPED fk_background_jobs_owner: orphaned owner_id value(s) found - fix the data first' AS warning;
    ELSE
      ALTER TABLE `background_jobs`
        ADD CONSTRAINT `fk_background_jobs_owner`
        FOREIGN KEY (`owner_id`) REFERENCES `users` (`id`);
    END IF;
  END IF;

  -- 3. background_job_actions.owner_id -> users.id
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'background_job_actions'
      AND CONSTRAINT_NAME = 'fk_background_job_actions_owner'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM `background_job_actions` a
      LEFT JOIN `users` u ON u.`id` = a.`owner_id`
      WHERE u.`id` IS NULL
    ) THEN
      SELECT 'SKIPPED fk_background_job_actions_owner: orphaned owner_id value(s) found - fix the data first' AS warning;
    ELSE
      ALTER TABLE `background_job_actions`
        ADD CONSTRAINT `fk_background_job_actions_owner`
        FOREIGN KEY (`owner_id`) REFERENCES `users` (`id`);
    END IF;
  END IF;

  -- 4. background_resource_slots.job_id -> background_jobs.id (nullable - a
  --    free slot has job_id = NULL, which FK constraints never check)
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'background_resource_slots'
      AND CONSTRAINT_NAME = 'fk_background_resource_slots_job'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM `background_resource_slots` s
      LEFT JOIN `background_jobs` j ON j.`id` = s.`job_id`
      WHERE s.`job_id` IS NOT NULL AND j.`id` IS NULL
    ) THEN
      SELECT 'SKIPPED fk_background_resource_slots_job: orphaned job_id value(s) found - fix the data first' AS warning;
    ELSE
      ALTER TABLE `background_resource_slots`
        ADD CONSTRAINT `fk_background_resource_slots_job`
        FOREIGN KEY (`job_id`) REFERENCES `background_jobs` (`id`);
    END IF;
  END IF;

  -- 5. trial_balance_upload_tokens.owner_id -> users.id
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'trial_balance_upload_tokens'
      AND CONSTRAINT_NAME = 'fk_trial_balance_upload_tokens_owner'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM `trial_balance_upload_tokens` t
      LEFT JOIN `users` u ON u.`id` = t.`owner_id`
      WHERE u.`id` IS NULL
    ) THEN
      SELECT 'SKIPPED fk_trial_balance_upload_tokens_owner: orphaned owner_id value(s) found - fix the data first' AS warning;
    ELSE
      ALTER TABLE `trial_balance_upload_tokens`
        ADD CONSTRAINT `fk_trial_balance_upload_tokens_owner`
        FOREIGN KEY (`owner_id`) REFERENCES `users` (`id`);
    END IF;
  END IF;
END//
DELIMITER ;

CALL `add_missing_foreign_keys`();
DROP PROCEDURE `add_missing_foreign_keys`;
