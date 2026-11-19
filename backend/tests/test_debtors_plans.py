"""Debtors Reports Auto Mailer mail plans: several scheduled mails in one application, each with its own reports,
as-on date, schedule, recipients, wording and send log."""

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
    Application, ApplicationEmailRecipient, EmailSettings, ScheduledReportMail, ScheduledReportMailLog,
    SystemEmailSettings, User,
)
from app.regional import IST
from app.services import scheduled_report_mail as mail
from app.services.debtors_mailer import auto_mail, plans
from app.services.debtors_mailer.models import MailPlan

APP = plans.APP_KEY
SCHEDULE = {
    "enabled": True, "mode": "weekly", "days": [0, 1, 2, 3, 4], "time": "08:30", "times": ["08:30"],
    "to": ["team@rdc.in"], "cc": [], "subject_template": "Debtors as on {date}", "intro_template": "Hello {date}",
    "signature": "", "send_when_unmapped": True,
}


class _PlanCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine, tables=[
            Application.__table__, ApplicationEmailRecipient.__table__, ScheduledReportMail.__table__,
            SystemEmailSettings.__table__, ScheduledReportMailLog.__table__, User.__table__,
            EmailSettings.__table__, MailPlan.__table__,
        ])
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.db = self.factory()
        for target, name, value in (
            (mail, "SessionLocal", self.factory),
            (mail.audit_middleware, "log_event", lambda *a, **k: None),
            (mail, "get_system_email_settings",
             lambda db: {"configured": True, "sender_email": "sys@rdc.in", "app_password": "x"}),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # "now" while settings are saved: a Monday, before any plan's send time (a test moves it where it needs)
        patcher = patch.object(mail, "now_ist", lambda: self.at(2026, 10, 19, 6, 0))
        patcher.start()
        self.addCleanup(patcher.stop)
        import app.database
        patcher = patch.object(app.database, "SessionLocal", self.factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sent = MagicMock()
        patcher = patch.object(mail, "send_mail", self.sent)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.db.close)

    def fresh(self, plan):
        self.db.expire_all()
        return plans.require(self.db, plan.id)

    def at(self, *args):
        return dt.datetime(*args, tzinfo=IST)


class CreatingPlansTests(_PlanCase):
    def test_a_new_plan_is_switched_off_and_starts_from_the_applications_standard_mail(self):
        plan = plans.create(self.db, "Daily team mail", "a@b.in")
        spec = mail.SPECS[APP]
        self.assertFalse(plan.enabled)
        self.assertEqual((plan.subject_template, plan.intro_template), (spec.default_subject, spec.default_intro))
        self.assertEqual(plans.selected(plan), ("ageing", "unapplied", "untagged"))     # all three, as always
        self.assertEqual(plan.as_on_mode, "today")
        self.assertEqual(plans.recipients(plan), {"to": [], "cc": []})

    def test_names_are_required_short_and_not_repeated_whatever_the_case(self):
        plans.create(self.db, "Month end", None)
        for bad, message in (("", "name"), ("   ", "name"), ("x" * 61, "at most"), ("month  END", "already a plan")):
            with self.subTest(bad=bad), self.assertRaisesRegex(plans.PlanError, message):
                plans.create(self.db, bad, None)

    def test_a_deleted_plans_name_can_be_used_again(self):
        plan = plans.create(self.db, "Month end", None)
        plans.delete(self.db, plan, None)
        self.assertEqual(plans.live_plans(self.db), [])
        self.assertEqual(plans.create(self.db, "Month end", None).name, "Month end")

    def test_there_is_a_limit_so_the_page_stays_readable(self):
        for number in range(plans.MAX_PLANS):
            plans.create(self.db, f"Plan {number}", None)
        with self.assertRaisesRegex(plans.PlanError, "At most"):
            plans.create(self.db, "One too many", None)

    def test_a_copy_has_the_content_schedule_recipients_and_wording_but_is_off(self):
        source = plans.create(self.db, "Daily", None)
        mail.save_settings(self.db, APP, {**SCHEDULE, "to": ["a@rdc.in"], "cc": ["c@rdc.in"]}, source.id)
        plans.save_reports(self.db, self.fresh(source), ["unapplied"], None)
        plans.save_as_on(self.db, self.fresh(source), "last_month_end", None, None)
        copy = plans.create(self.db, "Daily (copy)", None, copy_of=source.id)
        self.assertFalse(copy.enabled)
        self.assertEqual(plans.selected(copy), ("unapplied",))
        self.assertEqual(copy.as_on_mode, "last_month_end")
        self.assertEqual(plans.recipients(copy), {"to": ["a@rdc.in"], "cc": ["c@rdc.in"]})
        self.assertEqual((copy.send_days, copy.subject_template), (source.send_days, "Debtors as on {date}"))
        self.assertIsNone(copy.last_run_at)

    def test_renaming_checks_the_name_but_allows_keeping_it(self):
        one, two = plans.create(self.db, "One", None), plans.create(self.db, "Two", None)
        self.assertEqual(plans.rename(self.db, one, "One", None).name, "One")
        with self.assertRaisesRegex(plans.PlanError, "already a plan"):
            plans.rename(self.db, one, "two", None)

    def test_deleting_switches_it_off_and_keeps_its_log(self):
        plan = plans.create(self.db, "Daily", None)
        mail.save_settings(self.db, APP, SCHEDULE, plan.id)
        mail.run_mail_job(APP, "manual", lambda *a: mail.MailPayload(values={"date": "d"}), plan_id=plan.id)
        plans.delete(self.db, self.fresh(plan), None)
        self.assertIsNone(plans.get_plan(self.db, plan.id))
        self.assertFalse(self.db.get(MailPlan, plan.id).enabled)
        self.assertEqual(mail.log_view(self.db, APP, plan_id=plan.id)["total"], 1)


class ContentTests(_PlanCase):
    def test_at_least_one_known_report_must_be_picked(self):
        plan = plans.create(self.db, "P", None)
        with self.assertRaisesRegex(plans.PlanError, "at least one"):
            plans.save_reports(self.db, plan, [], None)
        with self.assertRaisesRegex(plans.PlanError, "Unknown"):
            plans.save_reports(self.db, plan, ["ageing", "nope"], None)
        view = plans.save_reports(self.db, plan, ["untagged", "ageing"], None)
        self.assertEqual(view["selected"], ["ageing", "untagged"])         # always in the mail's order
        self.assertFalse(view["all_selected"])

    def test_each_plan_has_its_own_choice(self):
        one, two = plans.create(self.db, "One", None), plans.create(self.db, "Two", None)
        plans.save_reports(self.db, one, ["unapplied"], None)
        self.assertEqual(plans.selected(self.fresh(one)), ("unapplied",))
        self.assertEqual(plans.selected(self.fresh(two)), ("ageing", "unapplied", "untagged"))


class AsOnTests(_PlanCase):
    TODAY = date(2026, 11, 13)

    def test_today_is_the_live_ageing_as_the_application_always_built_it(self):
        plan = plans.create(self.db, "P", None)
        self.assertIsNone(plans.as_on_for_run(plan, self.TODAY))              # nothing asked of the ERP's history
        self.assertEqual(plans.as_on_view(plan, self.TODAY)["resolved_date"], "2026-11-13")

    def test_last_month_end_is_the_last_day_of_the_month_before_and_follows_the_run_date(self):
        for today, expected in (
            (date(2026, 11, 13), date(2026, 10, 31)), (date(2026, 12, 1), date(2026, 11, 30)),
            (date(2026, 3, 31), date(2026, 2, 28)), (date(2028, 3, 1), date(2028, 2, 29)),
            (date(2027, 1, 15), date(2026, 12, 31)),
        ):
            self.assertEqual(plans.last_month_end(today), expected, today)
        plan = plans.create(self.db, "P", None)
        plans.save_as_on(self.db, plan, "last_month_end", None, None, self.TODAY)
        self.assertEqual(plans.as_on_for_run(plan, self.TODAY), date(2026, 10, 31))
        self.assertEqual(plans.as_on_for_run(plan, date(2026, 12, 2)), date(2026, 11, 30))   # worked out at every run

    def test_a_picked_date_is_used_as_it_is_but_not_a_future_one(self):
        plan = plans.create(self.db, "P", None)
        view = plans.save_as_on(self.db, plan, "date", date(2026, 9, 15), None, self.TODAY)
        self.assertEqual((view["mode"], view["resolved_date"]), ("date", "2026-09-15"))
        self.assertEqual(plans.as_on_for_run(plan, self.TODAY), date(2026, 9, 15))
        self.assertEqual(plans.save_as_on(self.db, plan, "date", self.TODAY, None, self.TODAY)["resolved_date"], "2026-11-13")
        with self.assertRaisesRegex(plans.PlanError, "future"):
            plans.save_as_on(self.db, plan, "date", date(2026, 11, 14), None, self.TODAY)

    def test_a_date_choice_needs_a_date_and_the_mode_must_be_known(self):
        plan = plans.create(self.db, "P", None)
        with self.assertRaisesRegex(plans.PlanError, "Pick the date"):
            plans.save_as_on(self.db, plan, "date", None, None, self.TODAY)
        with self.assertRaises(plans.PlanError):
            plans.save_as_on(self.db, plan, "yesterday", None, None, self.TODAY)

    def test_the_picked_date_is_kept_when_switching_to_another_choice(self):
        plan = plans.create(self.db, "P", None)
        plans.save_as_on(self.db, plan, "date", date(2026, 9, 15), None, self.TODAY)
        view = plans.save_as_on(self.db, plan, "last_month_end", None, None, self.TODAY)
        self.assertEqual((view["mode"], view["as_on_date"]), ("last_month_end", "2026-09-15"))

    def test_a_saved_date_that_is_no_longer_usable_is_reported_not_guessed(self):
        plan = plans.create(self.db, "P", None)
        plan.as_on_mode, plan.as_on_date = "date", date(2026, 12, 31)
        self.db.commit()
        view = plans.as_on_view(plan, self.TODAY)
        self.assertIsNone(view["resolved_date"])
        self.assertIn("future", view["problem"])
        with self.assertRaises(plans.PlanError):
            plans.as_on_for_run(plan, self.TODAY)

    def test_each_plan_has_its_own_date(self):
        one, two = plans.create(self.db, "Daily", None), plans.create(self.db, "Meeting", None)
        plans.save_as_on(self.db, two, "last_month_end", None, None, self.TODAY)
        self.assertIsNone(plans.as_on_for_run(self.fresh(one), self.TODAY))
        self.assertEqual(plans.as_on_for_run(self.fresh(two), self.TODAY), date(2026, 10, 31))


class ScheduleAndRecipientsTests(_PlanCase):
    def test_recipients_and_wording_are_kept_on_the_plan_not_on_the_application(self):
        one, two = plans.create(self.db, "One", None), plans.create(self.db, "Two", None)
        mail.save_settings(self.db, APP, {**SCHEDULE, "to": ["one@rdc.in"], "cc": ["boss@rdc.in"],
                                          "subject_template": "One {date}"}, one.id)
        mail.save_settings(self.db, APP, {**SCHEDULE, "to": ["two@rdc.in"]}, two.id)
        self.assertEqual(mail.settings_view(self.db, APP, one.id)["to"], ["one@rdc.in"])
        self.assertEqual(mail.settings_view(self.db, APP, one.id)["cc"], ["boss@rdc.in"])
        self.assertEqual(mail.settings_view(self.db, APP, two.id)["to"], ["two@rdc.in"])
        self.assertEqual(mail.settings_view(self.db, APP, one.id)["subject_template"], "One {date}")
        self.assertEqual(mail.settings_view(self.db, APP, two.id)["subject_template"], "Debtors as on {date}")
        self.assertEqual(self.db.query(ApplicationEmailRecipient).count(), 0)       # nothing under Admin -> Recipients

    def test_a_plan_cannot_be_switched_on_without_a_recipient_or_with_an_address_in_both_lists(self):
        plan = plans.create(self.db, "P", None)
        with self.assertRaisesRegex(ValueError, "To recipient"):
            mail.save_settings(self.db, APP, {**SCHEDULE, "to": []}, plan.id)
        with self.assertRaisesRegex(ValueError, "both To and Cc"):
            mail.save_settings(self.db, APP, {**SCHEDULE, "to": ["a@rdc.in"], "cc": ["a@rdc.in"]}, plan.id)

    def test_the_settings_view_of_a_plan_says_the_mail_is_always_from_the_system_email(self):
        plan = plans.create(self.db, "P", None)
        view = mail.settings_view(self.db, APP, plan.id)
        self.assertTrue(view["sender_fixed"])
        self.assertEqual(view["sender_email"], "sys@rdc.in")

    def test_plans_are_scheduled_independently(self):
        morning, evening = plans.create(self.db, "Morning", None), plans.create(self.db, "Evening", None)
        mail.save_settings(self.db, APP, {**SCHEDULE, "time": "08:30", "times": ["08:30"]}, morning.id)
        mail.save_settings(self.db, APP, {**SCHEDULE, "time": "17:00", "times": ["17:00"], "to": ["e@rdc.in"]},
                           evening.id)
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 19, 8, 31)),                 patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [f"{APP}#{morning.id}"])      # only the morning plan is due
            self.assertEqual(mail.enqueue_due_checks(), [])                          # and not again
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(submit.call_args.kwargs["plan_id"], morning.id)
        self.assertEqual(submit.call_args.kwargs["owner_id"], 0)
        self.assertEqual(submit.call_args.args[1], "scheduled")
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 19, 17, 1)),                 patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [f"{APP}#{evening.id}"])
        self.assertEqual(submit.call_args.kwargs["plan_id"], evening.id)

    def test_two_plans_due_at_the_same_time_each_send_their_own_mail(self):
        one, two = plans.create(self.db, "One", None), plans.create(self.db, "Two", None)
        for plan in (one, two):
            mail.save_settings(self.db, APP, SCHEDULE, plan.id)
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 21, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            started = mail.enqueue_due_checks()
        self.assertEqual(sorted(started), sorted([f"{APP}#{one.id}", f"{APP}#{two.id}"]))
        self.assertEqual(submit.call_count, 2)

    def test_a_plan_that_is_off_or_deleted_never_starts(self):
        off, gone = plans.create(self.db, "Off", None), plans.create(self.db, "Gone", None)
        mail.save_settings(self.db, APP, SCHEDULE, gone.id)
        plans.delete(self.db, self.fresh(gone), None)
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 21, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [])
        submit.assert_not_called()

    def test_the_applications_own_schedule_row_is_no_longer_run(self):
        """Before plans the application had a single schedule row; it must not fire alongside the plans."""
        mail.save_settings(self.db, APP, SCHEDULE)             # the old per-application row
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 21, 8, 31)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [])
        submit.assert_not_called()

    def test_the_list_describes_each_plan(self):
        plan = plans.create(self.db, "Meeting", None)
        mail.save_settings(self.db, APP, {**SCHEDULE, "to": ["a@rdc.in", "b@rdc.in"], "cc": ["c@rdc.in"]}, plan.id)
        plans.save_reports(self.db, self.fresh(plan), ["ageing", "untagged"], None)
        plans.save_as_on(self.db, self.fresh(plan), "last_month_end", None, None)
        listing = plans.list_view(self.db)
        self.assertEqual(len(listing["plans"]), 1)
        entry = listing["plans"][0]
        self.assertEqual((entry["name"], entry["enabled"], entry["reports"]), ("Meeting", True, ["ageing", "untagged"]))
        self.assertEqual((entry["to_count"], entry["cc_count"], entry["as_on"]["mode"]), (2, 1, "last_month_end"))
        self.assertEqual((entry["mode"], entry["days"], entry["times"]), ("weekly", [0, 1, 2, 3, 4], ["08:30"]))
        self.assertIsNotNone(entry["next_run"])


class RunningPlansTests(_PlanCase):
    def setUp(self):
        super().setUp()
        self.one, self.two = plans.create(self.db, "One", None), plans.create(self.db, "Two", None)
        mail.save_settings(self.db, APP, {**SCHEDULE, "to": ["one@rdc.in"], "cc": ["boss@rdc.in"],
                                          "subject_template": "One {date}"}, self.one.id)
        mail.save_settings(self.db, APP, {**SCHEDULE, "to": ["two@rdc.in"], "subject_template": "Two {date}"},
                           self.two.id)

    def test_a_run_goes_to_its_own_plans_recipients_with_its_own_wording(self):
        mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={"date": "31-Oct-2026"}),
                          plan_id=self.two.id)
        kwargs = self.sent.call_args.kwargs
        self.assertEqual((kwargs["to_addresses"], kwargs["cc_addresses"]), (["two@rdc.in"], []))
        self.assertEqual(kwargs["subject"], "Two 31-Oct-2026")
        self.assertEqual(kwargs["from_email"], "sys@rdc.in")

    def test_each_plan_has_its_own_log_and_its_own_last_run(self):
        mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={"date": "d"}), plan_id=self.one.id)
        mail.run_mail_job(APP, "manual", lambda *a: mail.MailPayload(values={}, hold_reason="2 unmapped"),
                          requested_by="a@b.in", plan_id=self.two.id)
        self.db.expire_all()
        one, two = mail.log_view(self.db, APP, plan_id=self.one.id), mail.log_view(self.db, APP, plan_id=self.two.id)
        self.assertEqual((one["total"], one["items"][0]["status"]), (1, "sent"))
        self.assertEqual((two["total"], two["items"][0]["status"]), (1, "held"))
        self.assertEqual(mail.log_view(self.db, APP)["total"], 2)                    # the application's whole history
        self.assertEqual(mail.settings_view(self.db, APP, self.one.id)["last"]["status"], "sent")
        self.assertEqual(mail.settings_view(self.db, APP, self.two.id)["last"]["status"], "held")

    def test_a_plan_that_is_running_does_not_stop_another_plan(self):
        started = []

        def build(snapshot, progress_cb=None, cancel_event=None):
            # while "One" is being built, "Two" can be run (they only wait on the ERP allowance, not on each other)
            if not started:
                started.append(1)
                mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={"date": "d"}),
                                  plan_id=self.two.id)
            return mail.MailPayload(values={"date": "d"})

        mail.run_mail_job(APP, "scheduled", build, plan_id=self.one.id)
        self.assertEqual(self.sent.call_count, 2)

    def test_the_same_plan_does_not_run_twice_at_once(self):
        def build(snapshot, progress_cb=None, cancel_event=None):
            with self.assertRaisesRegex(JobUserError, "already in progress"):
                mail.run_mail_job(APP, "manual", lambda *a: mail.MailPayload(values={}), plan_id=self.one.id)
            return mail.MailPayload(values={"date": "d"})

        mail.run_mail_job(APP, "scheduled", build, plan_id=self.one.id)
        self.assertEqual(self.sent.call_count, 1)

    def test_a_plan_deleted_before_its_run_stops_with_a_plain_message(self):
        plans.delete(self.db, self.fresh(self.one), None)
        with self.assertRaisesRegex(JobUserError, "does not exist"):
            mail.run_mail_job(APP, "scheduled", lambda *a: mail.MailPayload(values={}), plan_id=self.one.id)
        self.sent.assert_not_called()


class BuilderWiringTests(_PlanCase):
    def test_the_plans_reports_and_date_reach_the_builder(self):
        plan = plans.create(self.db, "Meeting", None)
        plans.save_reports(self.db, plan, ["unapplied", "untagged"], None)
        plans.save_as_on(self.db, self.fresh(plan), "date", date(2026, 9, 30), None)
        seen = {}

        def fake_build(snapshot, progress_cb=None, cancel_event=None):
            seen.update(snapshot)
            return mail.MailPayload(values={"date": "x"})

        with patch.object(auto_mail, "build", fake_build):
            payload = auto_mail._build_for(plan.id)({"send_when_unmapped": True})
        self.assertEqual(seen["reports"], ("unapplied", "untagged"))
        self.assertEqual(seen["as_on"], date(2026, 9, 30))
        self.assertEqual((payload.detail["plan"], payload.detail["as_on_mode"]), ("Meeting", "date"))

    def test_today_asks_the_erp_for_nothing_in_the_past(self):
        plan = plans.create(self.db, "Daily", None)
        seen = {}
        with patch.object(auto_mail, "build", lambda snapshot, *a: seen.update(snapshot) or mail.MailPayload(values={})):
            auto_mail._build_for(plan.id)({"send_when_unmapped": True})
        self.assertIsNone(seen["as_on"])

    def test_a_date_that_cannot_be_used_stops_the_run_with_a_plain_message(self):
        plan = plans.create(self.db, "Meeting", None)
        plan.as_on_mode, plan.as_on_date = "date", date(2999, 1, 1)
        self.db.commit()
        with self.assertRaisesRegex(JobUserError, "future"):
            auto_mail._build_for(plan.id)({"send_when_unmapped": True})

    def test_the_jobs_need_a_plan(self):
        for job in (auto_mail.run_auto_mail_job, auto_mail.run_preview_job):
            with self.assertRaises(JobUserError):
                if job is auto_mail.run_auto_mail_job:
                    job("manual", "a@b.in")
                else:
                    job(1, ["a@rdc.in"], [], "s", "i")


class ChangesForOneSendTests(_PlanCase):
    """A mail sent by hand can change the reports and the as-on rule for that send only."""
    TODAY = date(2026, 11, 13)

    def setUp(self):
        super().setUp()
        self.plan = plans.create(self.db, "Meeting", None)
        plans.save_reports(self.db, self.plan, ["ageing"], None)
        plans.save_as_on(self.db, self.fresh(self.plan), "last_month_end", None, None, self.TODAY)

    def test_nothing_changed_means_the_plan_as_saved(self):
        self.assertIsNone(plans.validate_overrides(None, None, None, self.TODAY))
        options = plans.run_options(self.plan.id, self.TODAY)
        self.assertEqual((options["reports"], options["as_on"], options["one_off"]),
                         (("ageing",), date(2026, 10, 31), False))

    def test_the_reports_can_be_changed_for_one_send(self):
        overrides = plans.validate_overrides(["untagged", "unapplied"], None, None, self.TODAY)
        self.assertEqual(overrides, {"reports": ["unapplied", "untagged"]})              # the mail's order
        options = plans.run_options(self.plan.id, self.TODAY, overrides)
        self.assertEqual((options["reports"], options["as_on"], options["one_off"]),
                         (("unapplied", "untagged"), date(2026, 10, 31), True))          # the date stays the plan's

    def test_each_as_on_rule_can_be_chosen_for_one_send(self):
        for mode, picked, expected_mode, expected in (
            ("today", None, "today", None),                                  # the live ageing
            ("last_month_end", None, "last_month_end", date(2026, 10, 31)),
            ("date", date(2026, 9, 15), "date", date(2026, 9, 15)),
        ):
            with self.subTest(mode=mode):
                overrides = plans.validate_overrides(None, mode, picked, self.TODAY)
                options = plans.run_options(self.plan.id, self.TODAY, overrides)
                self.assertEqual((options["as_on_mode"], options["as_on"]), (expected_mode, expected))
                self.assertEqual(options["reports"], ("ageing",))              # the reports stay the plan's

    def test_nothing_is_saved_on_the_plan(self):
        plans.run_options(self.plan.id, self.TODAY, plans.validate_overrides(["untagged"], "date", date(2026, 9, 15), self.TODAY))
        saved = self.fresh(self.plan)
        self.assertEqual((plans.selected(saved), saved.as_on_mode, saved.as_on_date), (("ageing",), "last_month_end", None))

    def test_bad_choices_are_refused_with_a_plain_message(self):
        for args, message in (
            (([], None, None), "at least one"), ((["nope"], None, None), "Unknown"),
            ((None, "yesterday", None), "Choose"), ((None, "date", None), "Pick the date"),
            ((None, "date", date(2026, 11, 14)), "future"), ((None, "date", date(1999, 1, 1)), "too far back"),
        ):
            with self.subTest(args=args), self.assertRaisesRegex(plans.PlanError, message):
                plans.validate_overrides(*args, self.TODAY)

    def test_the_builder_gets_them_and_the_log_says_what_was_changed(self):
        seen = {}

        def fake_build(snapshot, progress_cb=None, cancel_event=None):
            seen.update(snapshot)
            return mail.MailPayload(values={"date": "x"})

        overrides = plans.validate_overrides(["unapplied"], "date", date(2026, 9, 30), self.TODAY)
        with patch.object(auto_mail, "build", fake_build):
            payload = auto_mail._build_for(self.plan.id, overrides)({"send_when_unmapped": True})
        self.assertEqual((seen["reports"], seen["as_on"]), (("unapplied",), date(2026, 9, 30)))
        self.assertEqual(payload.detail["changed_for_this_send"], ["as_on_date", "as_on_mode", "reports"])
        self.assertEqual(payload.detail["as_on_mode"], "date")


class BringingAnInstallationAcrossTests(_PlanCase):
    def _legacy(self, **overrides):
        row = mail.get_or_create_settings(self.db, APP)
        for key, value in {"enabled": True, "send_days": "0,2", "send_time": "09:15",
                           "subject_template": "Old subject {date}", "intro_template": "Old text {date}",
                           "last_attempt_slot": "2026-10-19 09:15", **overrides}.items():
            setattr(row, key, value)
        self.db.commit()
        from app.services.mailer_shared import set_report_recipient_defaults
        set_report_recipient_defaults(self.db, APP, ["team@rdc.in"], ["boss@rdc.in"])
        self.db.commit()
        return row

    def test_the_existing_schedule_becomes_a_plan_that_carries_on_exactly_as_it_was(self):
        self._legacy()
        plan = plans.migrate_legacy(self.db)
        self.assertEqual(plan.name, "Debtors Reports")
        self.assertTrue(plan.enabled)
        self.assertEqual((plan.send_days, plan.send_time), ("0,2", "09:15"))
        self.assertEqual((plan.subject_template, plan.intro_template), ("Old subject {date}", "Old text {date}"))
        self.assertEqual(plans.recipients(plan), {"to": ["team@rdc.in"], "cc": ["boss@rdc.in"]})
        self.assertEqual((plan.as_on_mode, plans.selected(plan)), ("today", ("ageing", "unapplied", "untagged")))

    def test_it_is_not_sent_twice_and_the_old_row_stops_sending(self):
        self._legacy()
        plan = plans.migrate_legacy(self.db)
        self.assertEqual(plan.last_attempt_slot, "2026-10-19 09:15")              # the slot that already went out
        self.assertFalse(self.row_enabled())
        with patch("app.services.scheduled_report_mail.now_ist", return_value=self.at(2026, 10, 19, 9, 20)), \
                patch("app.jobs.submit_job", return_value="job") as submit:
            self.assertEqual(mail.enqueue_due_checks(), [])                          # that slot is already done
        submit.assert_not_called()

    def row_enabled(self):
        self.db.expire_all()
        return self.db.query(ScheduledReportMail).filter_by(app_key=APP).one().enabled

    def test_it_happens_once(self):
        self._legacy()
        self.assertIsNotNone(plans.migrate_legacy(self.db))
        self.assertIsNone(plans.migrate_legacy(self.db))
        self.assertEqual(len(plans.live_plans(self.db)), 1)

    def test_a_plan_the_person_deleted_is_not_brought_back_by_a_restart(self):
        self._legacy()
        plans.delete(self.db, plans.migrate_legacy(self.db), None)
        self.assertIsNone(plans.migrate_legacy(self.db))
        self.assertEqual(plans.live_plans(self.db), [])

    def test_an_installation_that_never_set_it_up_starts_with_no_plans(self):
        self.assertIsNone(plans.migrate_legacy(self.db))                             # no row at all
        mail.get_or_create_settings(self.db, APP)                                    # a row made by opening the page
        self.assertIsNone(plans.migrate_legacy(self.db))
        self.assertEqual(plans.live_plans(self.db), [])


if __name__ == "__main__":
    unittest.main()
