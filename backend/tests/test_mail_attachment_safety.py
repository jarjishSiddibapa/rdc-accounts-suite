"""Regression coverage for the production incident where a mail's listed
attachment(s) came through missing or empty and send_mail silently sent the
email anyway. Two distinct causes were found:

1. The scratch-cleanup sweep reclaiming the pending-preview directory before
   a slow "review, then confirm send" workflow got around to actually
   sending it - covered by test_scratch_sweep_mail_exemption.py.
2. A brief window right after the file is written where a fresh
   cross-process read sees 0 bytes even on an almost-immediate send (seen in
   production) - most likely antivirus on-access scanning holding the file
   momentarily, a known Windows phenomenon. read_attachment_bytes retries a
   few times before giving up, to ride out exactly this kind of transient
   race.

Either way, send_mail must now fail loudly, before ever touching SMTP, if an
attachment still can't be read as complete and non-empty after retrying."""
import os
import tempfile
import unittest
from unittest.mock import patch

from app.services.mailer_shared import MailAttachmentError, read_attachment_bytes, send_mail


class SendMailAttachmentSafetyTests(unittest.TestCase):
    def test_raises_before_touching_smtp_when_an_attachment_is_missing(self):
        with (
            patch("smtplib.SMTP") as mock_smtp,
            patch("app.services.mailer_shared.time.sleep"),  # skip the real retry delay
        ):
            with self.assertRaises(MailAttachmentError) as ctx:
                send_mail(
                    from_email="sender@example.com",
                    app_password="app-pass",
                    to_addresses=["to@example.com"],
                    cc_addresses=[],
                    subject="subject",
                    html_body="<p>body</p>",
                    attachments=["/nonexistent/path/Report.xlsx"],
                )
        self.assertIn("Report.xlsx", str(ctx.exception))
        mock_smtp.assert_not_called()

    def test_raises_before_touching_smtp_when_an_attachment_stays_empty_through_every_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = os.path.join(tmp, "Empty Report.xlsx")
            with open(empty_path, "wb"):
                pass  # 0-byte file, exactly like the incident's attachments

            with (
                patch("smtplib.SMTP") as mock_smtp,
                patch("app.services.mailer_shared.time.sleep"),
            ):
                with self.assertRaises(MailAttachmentError) as ctx:
                    send_mail(
                        from_email="sender@example.com",
                        app_password="app-pass",
                        to_addresses=["to@example.com"],
                        cc_addresses=[],
                        subject="subject",
                        html_body="<p>body</p>",
                        attachments=[empty_path],
                    )
            self.assertIn("Empty Report.xlsx", str(ctx.exception))
            mock_smtp.assert_not_called()

    def test_recovers_from_a_transient_empty_read_like_an_antivirus_scan_lock(self):
        """The exact production symptom: the file is momentarily unreadable
        (0 bytes) right after being written by another process, then becomes
        readable a moment later. This must NOT surface as a failure."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Report.xlsx")
            real_content = b"a real, complete report"
            with open(path, "wb") as fh:
                fh.write(real_content)

            call_count = {"n": 0}
            real_getsize = os.path.getsize

            def flaky_getsize(p):
                call_count["n"] += 1
                # First two checks see it as still-locked/empty; from the
                # third attempt on it reports its real, correct size.
                return 0 if call_count["n"] <= 2 else real_getsize(p)

            with (
                patch("app.services.mailer_shared.os.path.getsize", side_effect=flaky_getsize),
                patch("app.services.mailer_shared.time.sleep"),
            ):
                data = read_attachment_bytes(path)

        self.assertEqual(data, real_content)
        self.assertGreaterEqual(call_count["n"], 3)

    def test_sends_normally_when_every_attachment_is_present_and_non_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            good_path = os.path.join(tmp, "Report.xlsx")
            with open(good_path, "wb") as fh:
                fh.write(b"not really an xlsx but non-empty")

            with patch("smtplib.SMTP") as mock_smtp:
                smtp_instance = mock_smtp.return_value.__enter__.return_value
                send_mail(
                    from_email="sender@example.com",
                    app_password="app-pass",
                    to_addresses=["to@example.com"],
                    cc_addresses=[],
                    subject="subject",
                    html_body="<p>body</p>",
                    attachments=[good_path],
                )
            smtp_instance.sendmail.assert_called_once()
            sent_message = smtp_instance.sendmail.call_args.args[2]
            self.assertIn("Report.xlsx", sent_message)

    def test_empty_attachments_list_is_fine(self):
        with patch("smtplib.SMTP") as mock_smtp:
            smtp_instance = mock_smtp.return_value.__enter__.return_value
            send_mail(
                from_email="sender@example.com",
                app_password="app-pass",
                to_addresses=["to@example.com"],
                cc_addresses=[],
                subject="subject",
                html_body="<p>body</p>",
                attachments=[],
            )
        smtp_instance.sendmail.assert_called_once()


if __name__ == "__main__":
    unittest.main()
