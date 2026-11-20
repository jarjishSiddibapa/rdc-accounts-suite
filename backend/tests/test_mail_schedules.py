"""Mail schedules: an application's automatic mail can have several, each with its own timing, recipients, wording,
send log and - where the report can be as on a date - its own date (and, for the Debtors Reports, its own reports)."""

import datetime as dt
import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.jobs import JobUserError
from app.models import (
    Application, ApplicationEmailRecipient, EmailSettings, MailSchedule, ScheduledReportMail,
    ScheduledReportMailLog, SystemEmailSettings, User,
)
from app.permissions import REPORT_RECIPIENT_APP_KEYS
from app.regional import IST
from app.services import mail_schedules as ms
from app.services import scheduled_report_mail as mail
from app.services.debtors_mailer import auto_mail

APP = auto_mail.APP_KEY
UNTAGGED, GST, UNIDENTIFIED, SALES = (
    "untagged-invoices-report", "gst-invoice-adder", "unidentified-entries", "rdc-sales-register-report")
SCHEDULED_APPS = (APP, UNTAGGED, GST, UNIDENTIFIED, SALES)
SCHEDULE = {
    "enabled": True, "mode": "weekly", "days": [0, 1, 2, 3, 4], "time": "08:30", "times": ["08:30"],
    "to": ["team@rdc.in"], "cc": [], "subject_template": "Debtors as on {date}", "intro_template": "Hello {date}",
    "signature": "", "send_when_unmapped": True,
}


def settings(app_key=APP, **changes):
    """A valid save of an application's schedule settings (the wording uses only placeholders the app has)."""
    spec = mail.SPECS[app_key]
    return {**SCHEDULE, "subject_template": spec.default_subject, "intro_template": spec.default_intro, **changes}


class _Case(unittest.TestCase):
    TODAY = date(2026, 11, 13)

    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[
            Application.__table__, ApplicationEmailRecipient.__table__, ScheduledReportMail.__table__,
            SystemEmailSettings.__table__, ScheduledReportMailLog.__table__, User.__table__,
            EmailSettings.__table__, MailSchedule.__table__,
        ])
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.db = self.factory()
        import app.database
        for target, name, value in (
            (mail, "SessionLocal", self.factory),
            (app.database, "SessionLocal", self.factory),
            (mail.audit_middleware, "log_event", lambda *a, **k: None),
            (mail, "get_system_email_settings",
             lambda db: {"configured": True, "sender_email": "sys@rdc.in", "app_password": "x"}),
            # "now" while settings are saved: a Monday, before any schedule's send time (a test moves it)
            (mail, "now_ist", lambda: self.at(2026, 10, 19, 6, 0)),
            (ms, "today_ist", lambda: self.TODAY),
            (mail, "send_mail", MagicMock()),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent = mail.send_mail
        self.addCleanup(self.db.close)

    def fresh(self, row):
        self.db.expire_all()
        return ms.require(self.db, row.app_key, row.id)

    def at(self, *args):
        return dt.datetime(*args, tzinfo=IST)

    def save(self, row, **changes):
        return mail.save_settings(self.db, row.app_key, settings(row.app_key, **changes), row.id)


class CreatingSchedulesTests(_Case):
    def test_a_new_schedule_is_switched_off_and_starts_from_the_applications_standard_mail(self):
        for app_key in SCHEDULED_APPS:
            with self.subTest(app=app_key):
                row = ms.create(self.db, app_key, "Daily team mail", "a@b.in")
                spec = mail.SPECS[app_key]
                self.assertFalse(row.enabled)
                self.assertEqual((row.subject_template, row.intro_template), (spec.default_subject, spec.default_intro))
                self.assertEqual(row.as_on_mode, "today")
                self.assertEqual(ms.recipients(row), {"to": [], "cc": []})

    def test_the_debtors_reports_start_with_all_three_as_always(self):
        row = ms.create(self.db, APP, "P", None)
        self.assertEqual(ms.reports_selected(row), ("ageing", "unapplied", "untagged"))

    def test_names_are_required_short_and_not_repeated_whatever_the_case(self):
        ms.create(self.db, APP, "Month end", None)
        for bad, message in (("", "name"), ("   ", "name"), ("x" * 61, "at most"), ("month  END", "already a schedule")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ms.ScheduleError, message):
                ms.create(self.db, APP, bad, None)

    def test_the_same_name_can_be_used_by_another_application(self):
        ms.create(self.db, APP, "Month end", None)
        self.assertEqual(ms.create(self.db, UNTAGGED, "Month end", None).name, "Month end")

    def test_a_deleted_schedules_name_can_be_used_again(self):
        row = ms.create(self.db, APP, "Month end", None)
        ms.delete(self.db, row, None)
        self.assertEqual(ms.live(self.db, APP), [])
        self.assertEqual(ms.create(self.db, APP, "Month end", None).name, "Month end")

    def test_there_is_a_limit_so_the_page_stays_readable(self):
        for number in range(ms.MAX_SCHEDULES):
            ms.create(self.db, APP, f"Schedule {number}", None)
        with self.assertRaisesRegex(ms.ScheduleError, "At most"):
            ms.create(self.db, APP, "One too many", None)
        ms.create(self.db, UNTAGGED, "Another application has its own limit", None)

    def test_a_copy_has_the_content_timing_recipients_and_wording_but_is_off(self):
        source = ms.create(self.db, APP, "Daily", None)
        self.save(source, to=["a@rdc.in"], cc=["c@rdc.in"], reports=["unapplied"], as_on_mode="last_month_end")
        copy = ms.create(self.db, APP, "Daily (copy)", None, copy_of=source.id)
        self.assertFalse(copy.enabled)
        self.assertEqual(ms.reports_selected(copy), ("unapplied",))
        self.assertEqual(copy.as_on_mode, "last_month_end")
        self.assertEqual(ms.recipients(copy), {"to": ["a@rdc.in"], "cc": ["c@rdc.in"]})
        self.assertEqual(copy.send_days, source.send_days)
        self.assertIsNone(copy.last_run_at)

    def test_a_schedule_of_another_application_cannot_be_copied_or_opened(self):
        other = ms.create(self.db, UNTAGGED, "Other", None)
        with self.assertRaises(ms.ScheduleError):
            ms.create(self.db, APP, "Copy", None, copy_of=other.id)
        self.assertIsNone(ms.get(self.db, APP, other.id))

    def test_renaming_checks_the_name_but_allows_keeping_it(self):
        one = ms.create(self.db, APP, "One", None)
        ms.create(self.db, APP, "Two", None)
        self.assertEqual(ms.rename(self.db, one, "One", None).name, "One")
        with self.assertRaisesRegex(ms.ScheduleError, "already a schedule"):
            ms.rename(self.db, one, "two", None)

    def test_deleting_switches_it_off_and_keeps_its_log(self):
        row = ms.create(self.db, APP, "Daily", None)
        self.save(row)
        mail.run_mail_job(APP, "manual", lambda *a: mail.MailPayload(values={"date": "d"}), schedule_id=row.id)
        ms.delete(self.db, self.fresh(row), None)
        self.assertIsNone(ms.get(self.db, APP, row.id))
        self.assertFalse(self.db.get(MailSchedule, row.id).enabled)
        self.assertEqual(mail.log_view(self.db, APP, schedule_id=row.id)["total"], 1)


class ContentTests(_Case):
    """The date and (Debtors Reports) the reports are saved with the schedule's one Save."""

    def test_which_applications_have_which_choices(self):
        self.assertEqual({k for k in SCHEDULED_APPS if mail.SPECS[k].as_on}, {APP, UNTAGGED, GST, UNIDENTIFIED})
        self.assertEqual({k for k in SCHEDULED_APPS if mail.SPECS[k].report_choices}, {APP})

    def test_at_least_one_known_report_must_be_picked(self):
        row = ms.create(self.db, APP, "P", None)
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.save(row, reports=[])
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.save(row, reports=["ageing", "nope"])
        view = self.save(row, reports=["untagged", "ageing"])
        self.assertEqual([r["key"] for r in view["reports"] if r["selected"]], ["ageing", "untagged"])  # mail's order

    def test_each_schedule_has_its_own_choice(self):
        one, two = ms.create(self.db, APP, "One", None), ms.create(self.db, APP, "Two", None)
        self.save(one, reports=["unapplied"])
        self.assertEqual(ms.reports_selected(self.fresh(one)), ("unapplied",))
        self.assertEqual(ms.reports_selected(self.fresh(two)), ("ageing", "unapplied", "untagged"))

    def test_an_invalid_content_choice_stops_the_whole_save(self):
        row = ms.create(self.db, APP, "P", None)
        with self.assertRaises(ValueError):
            self.save(row, to=["new@rdc.in"], reports=[])
        self.assertEqual(ms.recipients(self.fresh(row)), {"to": [], "cc": []})

    def test_an_application_with_no_report_choice_or_date_ignores_them(self):
        row = ms.create(self.db, SALES, "P", None)
        self.save(row, as_on_mode="date", as_on_date="2026-09-15", reports=["x"])      # not offered: not applied
        self.assertEqual(self.fresh(row).as_on_mode, "today")
        self.assertNotIn("as_on", mail.settings_view(self.db, SALES, row.id))
        self.assertNotIn("reports", mail.settings_view(self.db, SALES, row.id))


class AsOnTests(_Case):
    def set_as_on(self, row, mode, picked=None):
        return self.save(row, enabled=False, as_on_mode=mode, as_on_date=picked)["as_on"]

    def test_today_is_the_report_as_the_application_always_built_it(self):
        row = ms.create(self.db, APP, "P", None)
        self.assertIsNone(ms.as_on_for_run(row, self.TODAY))                 # nothing asked of the ERP's history
        self.assertEqual(ms.as_on_view(row, self.TODAY)["resolved_date"], "2026-11-13")

    def test_last_month_end_is_the_last_day_of_the_month_before_and_follows_the_run_date(self):
        for today, expected in (
            (date(2026, 11, 13), date(2026, 10, 31)), (date(2026, 12, 1), date(2026, 11, 30)),
            (date(2026, 3, 31), date(2026, 2, 28)), (date(2028, 3, 1), date(2028, 2, 29)),
            (date(2027, 1, 15), date(2026, 12, 31)),
        ):
            self.assertEqual(ms.last_month_end(today), expected, today)
        row = ms.create(self.db, UNTAGGED, "P", None)
        self.set_as_on(row, "last_month_end")
        self.assertEqual(ms.as_on_for_run(self.fresh(row), self.TODAY), date(2026, 10, 31))
        self.assertEqual(ms.as_on_for_run(self.fresh(row), date(2026, 12, 2)), date(2026, 11, 30))  # at every run

    def test_a_picked_date_is_used_as_it_is_but_not_a_future_one(self):
        row = ms.create(self.db, GST, "P", None)
        view = self.set_as_on(row, "date", "2026-09-15")
        self.assertEqual((view["mode"], view["resolved_date"]), ("date", "2026-09-15"))
        self.assertEqual(ms.as_on_for_run(self.fresh(row), self.TODAY), date(2026, 9, 15))
        self.assertEqual(self.set_as_on(row, "date", "2026-11-13")["resolved_date"], "2026-11-13")
        with self.assertRaisesRegex(ValueError, "future"):
            self.set_as_on(row, "date", "2026-11-14")

    def test_a_date_choice_needs_a_date_and_the_mode_must_be_known(self):
        row = ms.create(self.db, UNIDENTIFIED, "P", None)
        with self.assertRaisesRegex(ValueError, "Pick the date"):
            self.set_as_on(row, "date")
        with self.assertRaises(ValueError):
            self.set_as_on(row, "yesterday")

    def test_the_picked_date_is_kept_when_switching_to_another_choice(self):
        row = ms.create(self.db, APP, "P", None)
        self.set_as_on(row, "date", "2026-09-15")
        view = self.set_as_on(row, "last_month_end")
        self.assertEqual((view["mode"], view["as_on_date"]), ("last_month_end", "2026-09-15"))

    def test_a_saved_date_that_is_no_longer_usable_is_reported_not_guessed(self):
        row = ms.create(self.db, APP, "P", None)
        row.as_on_mode, row.as_on_date = "date", date(2026, 12, 31)
        self.db.commit()
        view = ms.as_on_view(row, self.TODAY)
        self.assertIsNone(view["resolved_date"])
        self.assertIn("future", view["problem"])
        with self.assertRaises(ms.ScheduleError):
            ms.as_on_for_run(row, self.TODAY)

    def test_each_schedule_has_its_own_date(self):
        one, two = ms.create(self.db, APP, "Daily", None), ms.create(self.db, APP, "Meeting", None)
        self.set_as_on(two, "last_month_end")
        self.assertIsNone(ms.as_on_for_run(self.fresh(one), self.TODAY))
        self.assertEqual(ms.as_on_for_run(self.fresh(two), self.TODAY), date(2026, 10, 31))


class TimingAndRecipientsTests(_Case):
    def test_recipients_and_wording_are_kept_on_the_schedule_not_on_the_application(self):
        for app_key in SCHEDULED_APPS:
            with self.subTest(app=app_key):
                one, two = ms.create(self.db, app_key, "One", None), ms.create(self.db, app_key, "Two", None)
                self.save(one, to=["one@rdc.in"], cc=["boss@rdc.in"], subject_template="One {date}")
                self.save(two, to=["two@rdc.in"])
                view = mail.settings_view(self.db, app_key, one.id)
                self.assertEqual((view["to"], view["cc"], view["subject_template"]), (["one@rdc.in"], ["boss@rdc.in"], "One {date}"))
                self.assertEqual(mail.settings_view(self.db, app_key, two.id)["to"], ["two@rdc.in"])
                self.assertEqual(mail.settings_view(self.db, app_key, two.id)["subject_template"],
                                 mail.SPECS[app_key].default_subject)
        self.assertEqual(self.db.query(ApplicationEmailRecipient).count(), 0)       # nothing under Admin -> Recipients

    def test_these_applications_are_not_in_the_admin_recipients_list(self):
        self.assertFalse(set(SCHEDULED_APPS) & REPORT_RECIPIENT_APP_KEYS)

    def test_a_schedule_cannot_be_switched_on_without_a_recipient_or_with_an_address_in_both_lists(self):
        row = ms.create(self.db, APP, "P", None)
        with self.assertRaisesRegex(ValueError, "To recipient"):
            self.save(row, to=[])
        with self.assertRaisesRegex(ValueError, "both To and Cc"):
            self.save(row, to=["a@rdc.in"], cc=["a@rdc.in"])

    def test_a_fixed_sender_application_says_the_mail_is_always_from_the_system_email(self):
        for app_key in (APP, SALES, UNIDENTIFIED):
            row = ms.create(self.db, app_key, "P", None)
            view = mail.settings_view(self.db, app_key, row.id)
            self.assertTrue(view["sender_fixed"], app_key)
            self.assertEqual(view["sender_email"], "sys@rdc.in")

    def test_an_application_with_a_choice_of_sender_keeps_it_on_the_schedule(self):
        row = ms.create(self.db, UNTAGGED, "P", None)
        self.assertFalse(mail.settings_view(self.db, UNTAGGED, row.id)["sender_fixed"])
        with self.assertRaisesRegex(ValueError, "email sender"):
            self.save(row, sender_user_id=999)          # nobody with that id has an email set up
        self.assertIsNone(self.fresh(row).sender_user_id)

    def test_schedules_are_timed_independently(self):
        morning, evening = ms.create(self.db, APP, "Morning", None), ms.create(self.db, APP, "Evening", None)
        self.save(morning, time="08:30", times=["08:30"])
        self.save(evening, time="17:00", times=["17:00"], to=["e@rdc.in"])
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 19, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [f"{APP}#{morning.id}"])      # only the morning one is due
            self.assertEqual(mail.enqueue_due_checks(), [])                          # and not again
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(submit.call_args.kwargs["schedule_id"], morning.id)
        self.assertEqual(submit.call_args.kwargs["owner_id"], 0)
        self.assertEqual(submit.call_args.args[1], "scheduled")
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 19, 17, 1)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [f"{APP}#{evening.id}"])
        self.assertEqual(submit.call_args.kwargs["schedule_id"], evening.id)

    def test_every_application_with_schedules_starts_its_own_due_ones(self):
        started_rows = {}
        for app_key in SCHEDULED_APPS:
            started_rows[app_key] = ms.create(self.db, app_key, "Morning", None)
            self.save(started_rows[app_key])
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 21, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            started = mail.enqueue_due_checks()
        self.assertEqual(sorted(started), sorted(f"{k}#{row.id}" for k, row in started_rows.items()))
        self.assertEqual(submit.call_count, len(SCHEDULED_APPS))

    def test_two_schedules_due_at_the_same_time_each_send_their_own_mail(self):
        one, two = ms.create(self.db, APP, "One", None), ms.create(self.db, APP, "Two", None)
        for row in (one, two):
            self.save(row)
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 21, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            started = mail.enqueue_due_checks()
        self.assertEqual(sorted(started), sorted([f"{APP}#{one.id}", f"{APP}#{two.id}"]))
        self.assertEqual(submit.call_count, 2)

    def test_a_schedule_that_is_off_or_deleted_never_starts(self):
        ms.create(self.db, APP, "Off", None)
        gone = ms.create(self.db, APP, "Gone", None)
        self.save(gone)
        ms.delete(self.db, self.fresh(gone), None)
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 21, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [])
        submit.assert_not_called()

    def test_the_applications_own_old_schedule_row_is_no_longer_run(self):
        """Before schedules each application had a single settings row; it must not fire alongside the schedules."""
        for app_key in SCHEDULED_APPS:
            mail.save_settings(self.db, app_key, settings(app_key))             # the old per-application row
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 21, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [])
        submit.assert_not_called()

    def test_the_list_describes_each_schedule(self):
        row = ms.create(self.db, APP, "Meeting", None)
        self.save(row, to=["a@rdc.in", "b@rdc.in"], cc=["c@rdc.in"], reports=["ageing", "untagged"],
                  as_on_mode="last_month_end")
        listing = ms.list_view(self.db, APP)
        self.assertEqual(len(listing["schedules"]), 1)
        entry = listing["schedules"][0]
        self.assertEqual((entry["name"], entry["enabled"], entry["reports"]), ("Meeting", True, ["ageing", "untagged"]))
        self.assertEqual((entry["to_count"], entry["cc_count"], entry["as_on"]["mode"]), (2, 1, "last_month_end"))
        self.assertEqual((entry["mode"], entry["days"], entry["times"]), ("weekly", [0, 1, 2, 3, 4], ["08:30"]))
        self.assertIsNotNone(entry["next_run"])
        self.assertTrue(listing["has_as_on"])
        self.assertEqual(listing["report_labels"]["ageing"], "Ageing with GST Invoice Numbers")

    def test_the_list_of_an_application_without_those_choices_says_so(self):
        ms.create(self.db, SALES, "Daily", None)
        listing = ms.list_view(self.db, SALES)
        self.assertEqual((listing["has_as_on"], listing["report_labels"]), (False, None))
        self.assertEqual((listing["schedules"][0]["as_on"], listing["schedules"][0]["reports"]), (None, None))

    def test_one_applications_list_has_only_its_own_schedules(self):
        ms.create(self.db, APP, "Debtors one", None)
        ms.create(self.db, UNTAGGED, "Untagged one", None)
        self.assertEqual([s["name"] for s in ms.list_view(self.db, UNTAGGED)["schedules"]], ["Untagged one"])


class RunningSchedulesTests(_Case):
    def setUp(self):
        super().setUp()
        self.one, self.two = ms.create(self.db, APP, "One", None), ms.create(self.db, APP, "Two", None)
        self.save(self.one, to=["one@rdc.in"], cc=["boss@rdc.in"], subject_template="One {date}")
        self.save(self.two, to=["two@rdc.in"], subject_template="Two {date}")

    def test_a_run_goes_to_its_own_schedules_recipients_with_its_own_wording(self):
        mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={"date": "31-Oct-2026"}),
                          schedule_id=self.two.id)
        kwargs = self.sent.call_args.kwargs
        self.assertEqual((kwargs["to_addresses"], kwargs["cc_addresses"]), (["two@rdc.in"], []))
        self.assertEqual(kwargs["subject"], "Two 31-Oct-2026")
        self.assertEqual(kwargs["from_email"], "sys@rdc.in")

    def test_each_schedule_has_its_own_log_and_its_own_last_run(self):
        mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={"date": "d"}), schedule_id=self.one.id)
        mail.run_mail_job(APP, "manual", lambda *a: mail.MailPayload(values={}, hold_reason="2 unmapped"),
                          requested_by="a@b.in", schedule_id=self.two.id)
        self.db.expire_all()
        one = mail.log_view(self.db, APP, schedule_id=self.one.id)
        two = mail.log_view(self.db, APP, schedule_id=self.two.id)
        self.assertEqual((one["total"], one["items"][0]["status"]), (1, "sent"))
        self.assertEqual((two["total"], two["items"][0]["status"]), (1, "held"))
        self.assertEqual(mail.log_view(self.db, APP)["total"], 2)                    # the application's whole history
        self.assertEqual(mail.settings_view(self.db, APP, self.one.id)["last"]["status"], "sent")
        self.assertEqual(mail.settings_view(self.db, APP, self.two.id)["last"]["status"], "held")

    def test_a_schedule_that_is_running_does_not_stop_another_schedule(self):
        started = []

        def build(snapshot, progress_cb=None, cancel_event=None):
            # while "One" is being built, "Two" can be run (they only wait on the ERP allowance, not on each other)
            if not started:
                started.append(1)
                mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={"date": "d"}),
                                  schedule_id=self.two.id)
            return mail.MailPayload(values={"date": "d"})

        mail.run_mail_job(APP, "scheduled", build, schedule_id=self.one.id)
        self.assertEqual(self.sent.call_count, 2)

    def test_the_same_schedule_does_not_run_twice_at_once(self):
        def build(snapshot, progress_cb=None, cancel_event=None):
            with self.assertRaisesRegex(JobUserError, "already in progress"):
                mail.run_mail_job(APP, "manual", lambda *a: mail.MailPayload(values={}), schedule_id=self.one.id)
            return mail.MailPayload(values={"date": "d"})

        mail.run_mail_job(APP, "scheduled", build, schedule_id=self.one.id)
        self.assertEqual(self.sent.call_count, 1)

    def test_a_schedule_deleted_before_its_run_stops_with_a_plain_message(self):
        ms.delete(self.db, self.fresh(self.one), None)
        with self.assertRaisesRegex(JobUserError, "does not exist"):
            mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={}), schedule_id=self.one.id)
        self.sent.assert_not_called()


class BuilderWiringTests(_Case):
    def build_with(self, row, overrides=None):
        seen = {}

        def fake_build(snapshot, progress_cb=None, cancel_event=None):
            seen.update(snapshot)
            return mail.MailPayload(values={"date": "x"})

        with patch.object(ms, "today_ist", lambda: self.TODAY):
            return seen, ms.scheduled_build(fake_build, row.id, overrides)({"send_when_unmapped": True})

    def test_the_schedules_reports_and_date_reach_the_builder(self):
        row = ms.create(self.db, APP, "Meeting", None)
        self.save(row, reports=["unapplied", "untagged"], as_on_mode="date", as_on_date="2026-09-30")
        seen, payload = self.build_with(row)
        self.assertEqual(seen["reports"], ("unapplied", "untagged"))
        self.assertEqual(seen["as_on"], date(2026, 9, 30))
        self.assertEqual((payload.detail["schedule"], payload.detail["as_on_mode"]), ("Meeting", "date"))

    def test_today_asks_the_erp_for_nothing_in_the_past(self):
        for app_key in (APP, UNTAGGED, GST, UNIDENTIFIED):
            seen, _ = self.build_with(ms.create(self.db, app_key, "Daily", None))
            self.assertIsNone(seen["as_on"], app_key)

    def test_the_date_reaches_the_builder_of_each_application_that_has_one(self):
        for app_key in (UNTAGGED, GST, UNIDENTIFIED):
            with self.subTest(app=app_key):
                row = ms.create(self.db, app_key, "Month end", None)
                self.save(row, as_on_mode="last_month_end")
                seen, payload = self.build_with(row)
                self.assertEqual(seen["as_on"], date(2026, 10, 31))
                self.assertNotIn("reports", seen)
                self.assertEqual(payload.detail["as_on_mode"], "last_month_end")

    def test_an_application_without_a_date_is_never_given_one(self):
        row = ms.create(self.db, SALES, "Daily", None)
        seen, payload = self.build_with(row)
        self.assertIsNone(seen["as_on"])
        self.assertNotIn("as_on_mode", payload.detail)
        self.assertEqual(payload.detail["schedule"], "Daily")

    def test_a_date_that_cannot_be_used_stops_the_run_with_a_plain_message(self):
        row = ms.create(self.db, APP, "Meeting", None)
        row.as_on_mode, row.as_on_date = "date", date(2999, 1, 1)
        self.db.commit()
        with self.assertRaisesRegex(JobUserError, "future"):
            self.build_with(row)

    def test_the_jobs_need_a_schedule(self):
        from app.services.gst_invoice_adder import auto_mail as gst
        from app.services.sales_register import auto_mail as sales
        from app.services.unidentified_entries import auto_mail as unidentified
        from app.services.untagged_invoices import auto_mail as untagged
        for module in (auto_mail, untagged, gst, unidentified, sales):
            with self.subTest(module=module.__name__):
                with self.assertRaises(JobUserError):
                    module.run_auto_mail_job("manual", "a@b.in")
                with self.assertRaises(JobUserError):
                    module.run_preview_job(1, ["a@rdc.in"], [], "s", "i")


class ChangesForOneSendTests(_Case):
    """A mail sent by hand can change the date (and the Debtors Reports' reports) for that send only."""

    def setUp(self):
        super().setUp()
        self.row = ms.create(self.db, APP, "Meeting", None)
        self.save(self.row, enabled=False, reports=["ageing"], as_on_mode="last_month_end")

    def options(self, overrides=None, row=None):
        with patch.object(ms, "today_ist", lambda: self.TODAY):
            return ms.run_options((row or self.row).id, self.TODAY, overrides)

    def test_nothing_changed_means_the_schedule_as_saved(self):
        self.assertIsNone(ms.validate_overrides(APP, None, None, None, self.TODAY))
        options = self.options()
        self.assertEqual((options["reports"], options["as_on"], options["one_off"]),
                         (("ageing",), date(2026, 10, 31), False))

    def test_the_reports_can_be_changed_for_one_send(self):
        overrides = ms.validate_overrides(APP, ["untagged", "unapplied"], None, None, self.TODAY)
        self.assertEqual(overrides, {"reports": ["unapplied", "untagged"]})              # the mail's order
        options = self.options(overrides)
        self.assertEqual((options["reports"], options["as_on"], options["one_off"]),
                         (("unapplied", "untagged"), date(2026, 10, 31), True))          # the date stays the schedule's

    def test_each_as_on_rule_can_be_chosen_for_one_send(self):
        for mode, picked, expected_mode, expected in (
            ("today", None, "today", None),                                  # the live report
            ("last_month_end", None, "last_month_end", date(2026, 10, 31)),
            ("date", date(2026, 9, 15), "date", date(2026, 9, 15)),
        ):
            with self.subTest(mode=mode):
                overrides = ms.validate_overrides(APP, None, mode, picked, self.TODAY)
                options = self.options(overrides)
                self.assertEqual((options["as_on_mode"], options["as_on"]), (expected_mode, expected))
                self.assertEqual(options["reports"], ("ageing",))              # the reports stay the schedule's

    def test_the_date_can_be_changed_for_one_send_in_every_application_that_has_one(self):
        for app_key in (UNTAGGED, GST, UNIDENTIFIED):
            with self.subTest(app=app_key):
                row = ms.create(self.db, app_key, "P", None)
                overrides = ms.validate_overrides(app_key, None, "date", date(2026, 9, 15), self.TODAY)
                self.assertEqual(self.options(overrides, row)["as_on"], date(2026, 9, 15))
                self.assertNotIn("reports", self.options(overrides, row))

    def test_choices_an_application_does_not_have_are_ignored(self):
        self.assertIsNone(ms.validate_overrides(SALES, ["ageing"], "date", date(2026, 9, 15), self.TODAY))
        self.assertIsNone(ms.validate_overrides(UNTAGGED, ["ageing"], None, None, self.TODAY))

    def test_nothing_is_saved_on_the_schedule(self):
        self.options(ms.validate_overrides(APP, ["untagged"], "date", date(2026, 9, 15), self.TODAY))
        saved = self.fresh(self.row)
        self.assertEqual((ms.reports_selected(saved), saved.as_on_mode, saved.as_on_date), (("ageing",), "last_month_end", None))

    def test_bad_choices_are_refused_with_a_plain_message(self):
        for args, message in (
            (([], None, None), "at least one"), ((["nope"], None, None), "Unknown"),
            ((None, "yesterday", None), "Choose"), ((None, "date", None), "Pick the date"),
            ((None, "date", date(2026, 11, 14)), "future"), ((None, "date", date(1999, 1, 1)), "too far back"),
        ):
            with self.subTest(args=args), self.assertRaisesRegex(ms.ScheduleError, message):
                ms.validate_overrides(APP, *args, self.TODAY)

    def test_the_builder_gets_them_and_the_log_says_what_was_changed(self):
        seen = {}

        def fake_build(snapshot, progress_cb=None, cancel_event=None):
            seen.update(snapshot)
            return mail.MailPayload(values={"date": "x"})

        overrides = ms.validate_overrides(APP, ["unapplied"], "date", date(2026, 9, 30), self.TODAY)
        payload = ms.scheduled_build(fake_build, self.row.id, overrides)({"send_when_unmapped": True})
        self.assertEqual((seen["reports"], seen["as_on"]), (("unapplied",), date(2026, 9, 30)))
        self.assertEqual(payload.detail["changed_for_this_send"], ["as_on_date", "as_on_mode", "reports"])
        self.assertEqual(payload.detail["as_on_mode"], "date")


class BringingAnInstallationAcrossTests(_Case):
    def legacy(self, app_key=APP, **overrides):
        row = mail.get_or_create_settings(self.db, app_key)
        for key, value in {"enabled": True, "send_days": "0,2", "send_time": "09:15",
                           "subject_template": "Old subject {date}", "intro_template": "Old text {date}",
                           "last_attempt_slot": "2026-10-19 09:15", **overrides}.items():
            setattr(row, key, value)
        self.db.commit()
        from app.services.mailer_shared import set_report_recipient_defaults
        set_report_recipient_defaults(self.db, app_key, ["team@rdc.in"], ["boss@rdc.in"])
        self.db.commit()
        return row

    def test_the_existing_schedule_becomes_one_that_carries_on_exactly_as_it_was(self):
        self.legacy()
        row = ms.migrate_all_legacy(self.db) or ms.live(self.db, APP)[0]
        self.assertEqual(row.name, "Debtors Reports")
        self.assertTrue(row.enabled)
        self.assertEqual((row.send_days, row.send_time), ("0,2", "09:15"))
        self.assertEqual((row.subject_template, row.intro_template), ("Old subject {date}", "Old text {date}"))
        self.assertEqual(ms.recipients(row), {"to": ["team@rdc.in"], "cc": ["boss@rdc.in"]})
        self.assertEqual((row.as_on_mode, ms.reports_selected(row)), ("today", ("ageing", "unapplied", "untagged")))

    def test_every_application_that_had_it_set_up_is_brought_across_under_its_own_name(self):
        for app_key in SCHEDULED_APPS:
            self.legacy(app_key, sender_user_id=None)
        ms.migrate_all_legacy(self.db)
        for app_key in SCHEDULED_APPS:
            with self.subTest(app=app_key):
                rows = ms.live(self.db, app_key)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].name, "Debtors Reports" if app_key == APP else mail.SPECS[app_key].label)
                self.assertEqual(ms.recipients(rows[0]), {"to": ["team@rdc.in"], "cc": ["boss@rdc.in"]})

    def test_it_is_not_sent_twice_and_the_old_row_stops_sending(self):
        self.legacy(UNTAGGED)
        ms.migrate_all_legacy(self.db)
        self.assertEqual(ms.live(self.db, UNTAGGED)[0].last_attempt_slot, "2026-10-19 09:15")   # the slot that went
        self.db.expire_all()
        self.assertFalse(self.db.query(ScheduledReportMail).filter_by(app_key=UNTAGGED).one().enabled)
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 19, 9, 20)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [])                          # that slot is already done
        submit.assert_not_called()

    def test_the_chosen_sender_is_carried_across(self):
        self.legacy(GST, sender_user_id=7)
        ms.migrate_all_legacy(self.db)
        self.assertEqual(ms.live(self.db, GST)[0].sender_user_id, 7)

    def test_it_happens_once(self):
        self.legacy()
        self.assertIsNotNone(ms.migrate_legacy(self.db, APP, "Debtors Reports"))
        self.assertIsNone(ms.migrate_legacy(self.db, APP, "Debtors Reports"))
        self.assertEqual(len(ms.live(self.db, APP)), 1)

    def test_a_schedule_the_person_deleted_is_not_brought_back_by_a_restart(self):
        self.legacy()
        ms.delete(self.db, ms.migrate_legacy(self.db, APP), None)
        self.assertIsNone(ms.migrate_legacy(self.db, APP))
        self.assertEqual(ms.live(self.db, APP), [])

    def test_an_installation_that_never_set_it_up_starts_with_one_switched_off_schedule(self):
        for app_key in SCHEDULED_APPS:
            with self.subTest(app=app_key):
                mail.get_or_create_settings(self.db, app_key)            # a row made by opening the page, never set up
                row = ms.migrate_legacy(self.db, app_key)
                spec = mail.SPECS[app_key]
                self.assertFalse(row.enabled)
                self.assertEqual((row.subject_template, row.intro_template), (spec.default_subject, spec.default_intro))
                self.assertEqual(ms.recipients(row), {"to": [], "cc": []})
                self.assertEqual(len(ms.live(self.db, app_key)), 1)

    def test_a_fresh_installation_gets_the_same_default_schedule_once(self):
        self.assertIsNotNone(ms.migrate_legacy(self.db, UNTAGGED))                   # no old row at all
        self.assertIsNone(ms.migrate_legacy(self.db, UNTAGGED))
        self.assertEqual([row.name for row in ms.live(self.db, UNTAGGED)], ["Untagged Invoices Report"])


if __name__ == "__main__":
    unittest.main()


class PeriodTests(_Case):
    """The Sales Register's report covers the automatic period, last month, or days a person picks - never more than
    one complete month plus the first 8 days of the next."""

    def set_period(self, row, mode, first=None, last=None):
        return self.save(row, enabled=False, period_mode=mode, period_from=first, period_to=last)["period"]

    def test_only_the_sales_register_has_a_period_choice(self):
        self.assertTrue(ms.list_view(self.db, SALES)["has_period"])
        for app_key in (APP, UNTAGGED, GST, UNIDENTIFIED):
            self.assertFalse(ms.list_view(self.db, app_key)["has_period"], app_key)
        row = ms.create(self.db, GST, "P", None)
        self.save(row, enabled=False, period_mode="range", period_from="2026-10-01", period_to="2026-10-31")
        self.assertIsNone(ms.summary(self.db, self.fresh(row))["period"])        # ignored, nothing stored
        self.assertEqual(self.fresh(row).period_mode, "auto")

    def test_automatic_is_the_report_as_it_always_was(self):
        row = ms.create(self.db, SALES, "Daily", None)
        self.assertIsNone(ms.period_for_run(row, self.TODAY))
        view = ms.period_view(row, self.TODAY)
        self.assertEqual((view["mode"], [c["label"] for c in view["covers"]]), ("auto", ["Current Month MTD"]))
        self.assertEqual(view["extra_days"], 8)

    def test_last_month_is_the_whole_previous_month_and_follows_the_run_date(self):
        row = ms.create(self.db, SALES, "Month end", None)
        self.set_period(row, "last_month")
        self.assertEqual(ms.period_for_run(self.fresh(row), self.TODAY), (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertEqual(ms.period_for_run(self.fresh(row), date(2027, 1, 5)), (date(2026, 12, 1), date(2026, 12, 31)))

    def test_a_picked_month_or_days_are_used_as_picked(self):
        row = ms.create(self.db, SALES, "October", None)
        view = self.set_period(row, "range", "2026-10-01", "2026-10-31")
        self.assertEqual([c["label"] for c in view["covers"]], ["October 2026"])
        self.assertEqual(ms.period_for_run(self.fresh(row), self.TODAY), (date(2026, 10, 1), date(2026, 10, 31)))
        view = self.set_period(row, "range", "2026-10-10", "2026-10-20")
        self.assertEqual([c["label"] for c in view["covers"]], ["October 2026 (10 to 20)"])

    def test_a_complete_month_plus_eight_days_is_the_most_that_can_be_picked(self):
        row = ms.create(self.db, SALES, "October", None)
        view = self.set_period(row, "range", "2026-10-01", "2026-11-08")                 # exactly the limit
        self.assertEqual([c["label"] for c in view["covers"]], ["October 2026", "November 2026 (1 to 8)"])
        with self.assertRaisesRegex(ValueError, "one complete month plus the first 8 days"):
            self.set_period(row, "range", "2026-10-01", "2026-11-09")                    # a day too many
        with self.assertRaisesRegex(ValueError, "08-Nov-2026"):                          # and it says what is allowed
            self.set_period(row, "range", "2026-10-01", "2026-11-12")
        self.set_period(row, "range", "2026-10-15", "2026-11-08")                        # from mid-month: fine, shorter
        with self.assertRaises(ValueError):
            self.set_period(row, "range", "2026-10-15", "2026-11-09")
        with self.assertRaises(ValueError):                                              # two whole months
            self.set_period(row, "range", "2026-09-01", "2026-10-31")

    def test_the_limit_follows_the_length_of_the_month(self):
        today = date(2027, 3, 20)
        self.assertEqual(ms.report_periods.latest_end(date(2027, 2, 1), today), date(2027, 3, 8))   # February: 28 + 8
        self.assertEqual(ms.report_periods.latest_end(date(2028, 2, 1), date(2028, 4, 1)), date(2028, 3, 8))  # leap year
        self.assertEqual(ms.report_periods.latest_end(date(2026, 12, 1), date(2027, 3, 1)), date(2027, 1, 8))
        # never past yesterday
        self.assertEqual(ms.report_periods.latest_end(date(2026, 11, 1), self.TODAY), date(2026, 11, 12))

    def test_only_completed_days_and_sensible_days_can_be_picked(self):
        row = ms.create(self.db, SALES, "P", None)
        with self.assertRaisesRegex(ValueError, "completed days"):
            self.set_period(row, "range", "2026-11-01", "2026-11-13")                    # today is not complete
        with self.assertRaisesRegex(ValueError, "before the first"):
            self.set_period(row, "range", "2026-10-20", "2026-10-10")
        with self.assertRaisesRegex(ValueError, "Pick the first and the last day"):
            self.set_period(row, "range", "2026-10-01", None)
        with self.assertRaisesRegex(ValueError, "Pick the first and the last day"):
            self.set_period(row, "range", "not a date", "2026-10-31")
        with self.assertRaises(ValueError):
            self.set_period(row, "forever")
        self.set_period(row, "range", "2026-11-12", "2026-11-12")                        # yesterday alone is fine

    def test_a_refused_period_stops_the_whole_save(self):
        row = ms.create(self.db, SALES, "P", None)
        with self.assertRaises(ValueError):
            self.save(row, enabled=False, subject_template="Changed {date}", period_mode="range",
                      period_from="2026-09-01", period_to="2026-10-31")
        self.assertNotEqual(self.fresh(row).subject_template, "Changed {date}")

    def test_the_picked_days_are_kept_when_switching_to_another_choice(self):
        row = ms.create(self.db, SALES, "P", None)
        self.set_period(row, "range", "2026-10-01", "2026-10-31")
        view = self.set_period(row, "auto")
        self.assertEqual((view["mode"], view["from"], view["to"]), ("auto", "2026-10-01", "2026-10-31"))

    def test_each_schedule_has_its_own_period_and_a_copy_starts_with_it(self):
        one, two = ms.create(self.db, SALES, "Daily", None), ms.create(self.db, SALES, "October pack", None)
        self.set_period(two, "range", "2026-10-01", "2026-10-31")
        self.assertIsNone(ms.period_for_run(self.fresh(one), self.TODAY))
        self.assertEqual(ms.period_for_run(self.fresh(two), self.TODAY), (date(2026, 10, 1), date(2026, 10, 31)))
        copy = ms.create(self.db, SALES, "October pack 2", None, copy_of=two.id)
        self.assertEqual(ms.period_for_run(copy, self.TODAY), (date(2026, 10, 1), date(2026, 10, 31)))

    def test_a_saved_period_that_can_no_longer_run_is_reported_not_guessed(self):
        row = ms.create(self.db, SALES, "P", None)
        row.period_mode, row.period_from, row.period_to = "range", date(2026, 9, 1), date(2026, 10, 31)
        self.db.commit()
        self.assertIn("one complete month", ms.period_view(row, self.TODAY)["problem"])
        with self.assertRaises(ms.ScheduleError):
            ms.period_for_run(row, self.TODAY)

    def test_the_period_reaches_the_builder_and_the_runs_detail(self):
        seen = {}

        def fake_build(snapshot, progress_cb=None, cancel_event=None):
            seen.clear()
            seen.update(snapshot)
            return mail.MailPayload(values={"date": "x"})

        row = ms.create(self.db, SALES, "October", None)
        with patch.object(ms, "today_ist", lambda: self.TODAY):
            payload = ms.scheduled_build(fake_build, row.id)({"send_when_unmapped": True})
            self.assertIsNone(seen["period"])
            self.assertEqual(payload.detail["period_mode"], "auto")
            self.set_period(row, "range", "2026-10-01", "2026-10-31")
            payload = ms.scheduled_build(fake_build, row.id)({"send_when_unmapped": True})
            self.assertEqual(seen["period"], (date(2026, 10, 1), date(2026, 10, 31)))
            self.assertEqual(payload.detail["period_mode"], "range")
            # an application without a period is never given one
            other = ms.create(self.db, GST, "G", None)
            ms.scheduled_build(fake_build, other.id)({"send_when_unmapped": True})
            self.assertNotIn("period", seen)

    def test_a_mail_sent_by_hand_can_use_other_days_without_touching_the_schedule(self):
        row = ms.create(self.db, SALES, "Daily", None)
        overrides = ms.validate_overrides(SALES, None, None, None, self.TODAY, period_mode="range",
                                          period_from=date(2026, 9, 1), period_to=date(2026, 9, 30))
        self.assertEqual(overrides, {"period_mode": "range", "period_from": "2026-09-01", "period_to": "2026-09-30"})
        with patch.object(ms, "today_ist", lambda: self.TODAY):
            self.assertEqual(ms.run_options(row.id, self.TODAY, overrides)["period"], (date(2026, 9, 1), date(2026, 9, 30)))
            self.assertIsNone(ms.run_options(row.id, self.TODAY)["period"])
        self.assertEqual(self.fresh(row).period_mode, "auto")                                # nothing saved
        self.assertIsNone(ms.validate_overrides(SALES, None, None, None, self.TODAY))        # nothing changed
        with self.assertRaisesRegex(ValueError, "one complete month plus the first 8 days"):
            ms.validate_overrides(SALES, None, None, None, self.TODAY, period_mode="range",
                                  period_from=date(2026, 8, 1), period_to=date(2026, 9, 30))
        self.assertIsNone(ms.validate_overrides(GST, None, None, None, self.TODAY, period_mode="range",
                                                period_from=date(2026, 8, 1), period_to=date(2026, 9, 30)))


CASHFLOW = "ultrafine-cashflow"


class MonthTests(_Case):
    """The Ultrafine Cashflow is mailed on schedules like the other reports; each says which month it is of."""

    def set_month(self, row, mode, value=None):
        return self.save(row, enabled=False, month_mode=mode, month_value=value)["month"]

    def test_the_cashflow_is_a_scheduled_report_with_a_month_choice_and_nothing_else_has_one(self):
        spec = mail.SPECS[CASHFLOW]
        self.assertTrue(spec.scheduled and spec.per_schedule and spec.month)
        self.assertTrue(ms.list_view(self.db, CASHFLOW)["has_month"])
        for app_key in (APP, UNTAGGED, GST, UNIDENTIFIED, SALES):
            self.assertFalse(ms.list_view(self.db, app_key)["has_month"], app_key)
        row = ms.create(self.db, GST, "M", None)
        self.save(row, enabled=False, month_mode="month", month_value="2026-10")
        self.assertIsNone(ms.summary(self.db, self.fresh(row))["month"])           # ignored, nothing stored
        self.assertEqual(self.fresh(row).month_mode, "current")

    def test_this_month_is_the_default_and_follows_the_run_date(self):
        row = ms.create(self.db, CASHFLOW, "Daily", None)
        self.assertEqual(ms.month_for_run(row, self.TODAY), "2026-11")
        self.assertEqual(ms.month_for_run(row, date(2027, 1, 2)), "2027-01")
        view = ms.month_view(row, self.TODAY)
        self.assertEqual((view["mode"], view["resolved"], view["current"], view["last"]),
                         ("current", "2026-11", "2026-11", "2026-10"))

    def test_last_month_is_worked_out_afresh_at_every_run(self):
        row = ms.create(self.db, CASHFLOW, "Month end", None)
        self.set_month(row, "last")
        self.assertEqual(ms.month_for_run(self.fresh(row), self.TODAY), "2026-10")
        self.assertEqual(ms.month_for_run(self.fresh(row), date(2027, 1, 5)), "2026-12")     # across a year end

    def test_a_picked_month_is_used_as_picked_and_can_be_the_current_one(self):
        row = ms.create(self.db, CASHFLOW, "September", None)
        self.assertEqual(self.set_month(row, "month", "2026-09")["resolved"], "2026-09")
        self.assertEqual(self.set_month(row, "month", "2026-11")["resolved"], "2026-11")

    def test_a_month_that_cannot_be_mailed_is_refused_and_stops_the_whole_save(self):
        row = ms.create(self.db, CASHFLOW, "M", None)
        for value, message in ((None, "Pick the month"), ("2026-13", "Pick the month"), ("Oct 2026", "Pick the month"),
                               ("2026-12", "not started yet"), ("1999-12", "too far back")):
            with self.assertRaisesRegex(ValueError, message):
                self.set_month(row, "month", value)
        with self.assertRaises(ValueError):
            self.set_month(row, "next")
        with self.assertRaises(ValueError):
            self.save(row, enabled=False, subject_template="Changed {date}", month_mode="month", month_value="2027-01")
        self.assertNotEqual(self.fresh(row).subject_template, "Changed {date}")

    def test_the_picked_month_is_kept_when_switching_to_another_choice(self):
        row = ms.create(self.db, CASHFLOW, "M", None)
        self.set_month(row, "month", "2026-09")
        view = self.set_month(row, "current")
        self.assertEqual((view["mode"], view["value"]), ("current", "2026-09"))

    def test_each_schedule_has_its_own_month_and_a_copy_starts_with_it(self):
        one, two = ms.create(self.db, CASHFLOW, "Daily", None), ms.create(self.db, CASHFLOW, "September pack", None)
        self.set_month(two, "month", "2026-09")
        self.assertEqual(ms.month_for_run(self.fresh(one), self.TODAY), "2026-11")
        self.assertEqual(ms.month_for_run(self.fresh(two), self.TODAY), "2026-09")
        copy = ms.create(self.db, CASHFLOW, "September pack 2", None, copy_of=two.id)
        self.assertEqual(ms.month_for_run(copy, self.TODAY), "2026-09")

    def test_a_new_cashflow_schedule_holds_the_mail_while_bank_lines_are_unmapped(self):
        self.assertFalse(ms.create(self.db, CASHFLOW, "C", None).send_when_unmapped)
        self.assertTrue(ms.create(self.db, GST, "G", None).send_when_unmapped)             # the others are unchanged
        row = ms.create(self.db, CASHFLOW, "C2", None)
        self.save(row, enabled=False, send_when_unmapped=True)
        self.assertTrue(self.fresh(row).send_when_unmapped)                                # and it can be changed

    def test_the_month_reaches_the_builder_and_the_runs_detail(self):
        seen = {}

        def fake_build(snapshot, progress_cb=None, cancel_event=None):
            seen.clear()
            seen.update(snapshot)
            return mail.MailPayload(values={"date": "x"})

        row = ms.create(self.db, CASHFLOW, "C", None)
        payload = ms.scheduled_build(fake_build, row.id)({"send_when_unmapped": False})
        self.assertEqual((seen["month"], payload.detail["month_mode"]), ("2026-11", "current"))
        self.set_month(row, "last")
        payload = ms.scheduled_build(fake_build, row.id)({"send_when_unmapped": False})
        self.assertEqual((seen["month"], payload.detail["month_mode"]), ("2026-10", "last"))
        other = ms.create(self.db, GST, "G", None)                         # an application without a month is never given one
        ms.scheduled_build(fake_build, other.id)({"send_when_unmapped": True})
        self.assertNotIn("month", seen)

    def test_a_mail_sent_by_hand_can_be_of_another_month_without_touching_the_schedule(self):
        row = ms.create(self.db, CASHFLOW, "Daily", None)
        overrides = ms.validate_overrides(CASHFLOW, None, None, None, self.TODAY, month_mode="month",
                                          month_value="2026-08")
        self.assertEqual(overrides, {"month_mode": "month", "month_value": "2026-08"})
        self.assertEqual(ms.run_options(row.id, self.TODAY, overrides)["month"], "2026-08")
        self.assertEqual(ms.run_options(row.id, self.TODAY)["month"], "2026-11")
        self.assertEqual(self.fresh(row).month_mode, "current")                            # nothing saved
        self.assertIsNone(ms.validate_overrides(CASHFLOW, None, None, None, self.TODAY))   # nothing changed
        with self.assertRaisesRegex(ValueError, "not started yet"):
            ms.validate_overrides(CASHFLOW, None, None, None, self.TODAY, month_mode="month", month_value="2027-02")
        self.assertIsNone(ms.validate_overrides(GST, None, None, None, self.TODAY, month_mode="month",
                                                month_value="2026-08"))

    def test_a_saved_month_that_can_no_longer_run_is_reported_not_guessed(self):
        row = ms.create(self.db, CASHFLOW, "M", None)
        row.month_mode, row.month_value = "month", "2027-05"
        self.db.commit()
        self.assertIn("not started yet", ms.month_view(row, self.TODAY)["problem"])
        with self.assertRaises(ms.ScheduleError):
            ms.month_for_run(row, self.TODAY)


class CashflowMailTests(_Case):
    """The scheduler runs the Ultrafine Cashflow's schedules and the build takes its month from the schedule."""

    def test_the_scheduler_starts_a_due_cashflow_schedule(self):
        row = ms.create(self.db, CASHFLOW, "Mornings", None)
        self.save(row, enabled=True, days=[0, 1, 2, 3, 4, 5, 6], time="08:30", times=["08:30"])
        started = []
        fake = MagicMock(side_effect=lambda job, *a, **k: started.append((job, k)) or "job")
        with patch.object(mail, "now_ist", lambda: self.at(2026, 10, 19, 9, 0)), \
                patch("app.jobs.submit_job", fake):
            names = mail.enqueue_due_checks()
        self.assertEqual(names, [f"{CASHFLOW}#{row.id}"])
        from app.services.ultrafine_cashflow import auto_mail as cashflow_mail
        self.assertIs(started[0][0], cashflow_mail.run_auto_mail_job)
        self.assertEqual(started[0][1]["schedule_id"], row.id)

    def test_a_schedule_with_no_month_to_mail_is_held_with_the_reason(self):
        from app.services.ultrafine_cashflow import auto_mail as cashflow_mail

        row = ms.create(self.db, CASHFLOW, "C", None)
        self.save(row, enabled=False, month_mode="last", send_when_unmapped=False)
        seen = {}

        def fake_generate(db, month, user_id, allow_unmapped=False):
            seen.update(month=month, allow_unmapped=allow_unmapped)
            return {"month": month, "status": "no_data"}

        with patch.object(cashflow_mail, "generate_month", fake_generate):
            result = mail.run_mail_job(CASHFLOW, "scheduled", ms.scheduled_build(cashflow_mail.build, row.id),
                                       schedule_id=row.id)
        self.assertEqual(seen, {"month": "2026-10", "allow_unmapped": False})
        self.assertEqual(result["status"], "held")
        self.assertIn("no bank lines", result["message"])
        self.assertEqual(mail.log_view(self.db, CASHFLOW, schedule_id=row.id)["items"][0]["detail"]["month"], "2026-10")
        self.sent.assert_not_called()
        self.assertEqual(mail.log_view(self.db, CASHFLOW, schedule_id=row.id)["items"][0]["status"], "held")

    def test_every_scheduled_application_has_a_registered_background_job(self):
        # the queue only runs registered task functions; an unregistered one fails when the scheduler starts it
        from app import jobs

        for app_key, spec in mail.SPECS.items():
            if spec.scheduled:
                with self.subTest(app_key=app_key):
                    self.assertEqual(jobs._task_name(mail._job_for(app_key)).split(":")[1], "run_auto_mail_job")
        self.assertTrue(mail.SPECS[CASHFLOW].scheduled)

    def test_a_build_without_a_month_asks_for_one(self):
        from app.services.ultrafine_cashflow import auto_mail as cashflow_mail

        with self.assertRaisesRegex(JobUserError, "Choose the month"):
            cashflow_mail.build({"send_when_unmapped": False})
