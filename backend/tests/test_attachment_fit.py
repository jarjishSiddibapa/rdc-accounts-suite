"""The one rule for every application's mail: compact, then .xlsb, then split as the last resort, never losing data."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs import JobUserError
from app.services import attachment_fit
from app.services import scheduled_report_mail as mail


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        limit = patch.object(mail, "MAX_ATTACHMENT_BYTES", 1000)
        limit.start()
        self.addCleanup(limit.stop)
        no_shrink = (
            patch.object(attachment_fit.xlsx_compact, "compact_xlsx_in_place"),
            patch.object(attachment_fit.xlsb_convert, "convert_to_xlsb", lambda s, d: False),
        )
        for p in no_shrink:
            p.start()
            self.addCleanup(p.stop)

    def file(self, name, size):
        path = self.dir / name
        path.write_bytes(b"x" * size)
        return path

    def rows_splitter(self, rows, bytes_per_row=10, stem="whole"):
        """A splitter over `rows` rows where each row is `bytes_per_row` bytes; records what each part holds."""
        written = []

        def write_slice(first, last, path):
            path.write_bytes(b"x" * ((last - first) * bytes_per_row))
            written.append((first, last))

        return attachment_fit.row_splitter(rows, write_slice, self.dir, stem), written


class RowSplitterTests(_Base):
    def test_every_row_lands_in_exactly_one_part_in_order(self):
        for count in (2, 3, 7):
            splitter, written = self.rows_splitter(100)
            parts = splitter(count)
            self.assertEqual(len(parts), len(written))
            self.assertEqual(written[0][0], 0)
            self.assertEqual(written[-1][1], 100)
            for (_, last), (first, _) in zip(written, written[1:]):
                self.assertEqual(first, last)
            self.assertEqual(sum(last - first for first, last in written), 100)
            written.clear()

    def test_it_never_makes_an_empty_part_and_a_single_row_cannot_be_split(self):
        splitter, written = self.rows_splitter(5)
        splitter(4)
        self.assertTrue(all(last > first for first, last in written))
        with self.assertRaises(attachment_fit.CannotSplit):
            self.rows_splitter(1)[0](2)


class FitTests(_Base):
    def test_files_that_fit_together_go_in_one_mail_untouched(self):
        a, b = self.file("a.xlsx", 400), self.file("b.xlsx", 500)
        groups, how, finals = attachment_fit.fit_together([a, b])
        self.assertEqual(groups, [[str(a), str(b)]])
        self.assertEqual(how, "complete workbooks")

    def test_files_that_do_not_fit_together_but_each_fit_alone_are_packed_not_split(self):
        a, b, c = self.file("a.xlsx", 600), self.file("b.xlsx", 600), self.file("c.xlsx", 300)
        groups, how, _ = attachment_fit.fit_together([a, b, c])
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])               # a+c share, b alone: fewest mails
        self.assertEqual(sorted(Path(p).name for g in groups for p in g), ["a.xlsx", "b.xlsx", "c.xlsx"])

    def test_one_file_over_the_limit_on_its_own_is_split_and_all_its_rows_are_kept(self):
        whole = self.file("whole.xlsx", 3000)               # 300 rows of 10 bytes: three times the limit
        splitter, written = self.rows_splitter(300)
        groups, how, finals = attachment_fit.fit_together([whole], splitters={whole: splitter})
        self.assertEqual(sum(last - first for first, last in written), 300)    # no row dropped
        self.assertEqual(sum(1 for g in groups for _ in g), len(finals))
        self.assertTrue(all(p.stat().st_size <= 1000 for p in finals))
        self.assertIn("split", how)
        self.assertGreaterEqual(len(groups), 3)

    def test_it_starts_near_the_fewest_parts_instead_of_many_small_ones(self):
        whole = self.file("whole.xlsx", 3000)
        splitter, written = self.rows_splitter(300)
        attachment_fit.fit_together([whole], splitters={whole: splitter})
        self.assertLessEqual(len(written), 5)                # about 3000 / (1000 * 0.85) = 4 parts, one round

    def test_parts_that_are_still_too_big_are_redone_with_more_parts(self):
        whole = self.file("whole.xlsx", 3000)
        splitter, written = self.rows_splitter(300, bytes_per_row=10)
        calls = []

        def lopsided(count):
            calls.append(count)
            return splitter(count if len(calls) > 1 else 2)   # the first attempt makes too few, too big parts

        groups, _, finals = attachment_fit.fit_together([whole], splitters={whole: lopsided})
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(p.stat().st_size <= 1000 for p in finals))
        self.assertEqual(sum(1 for f in self.dir.glob("whole_Part_*")), len(finals))   # the failed attempt was removed

    def test_a_file_over_the_limit_that_cannot_be_split_stops_with_a_clear_message(self):
        whole = self.file("whole.xlsx", 3000)
        with self.assertRaises(JobUserError) as caught:
            attachment_fit.fit_together([whole])
        self.assertIn("cannot be split", str(caught.exception))
        self.assertIn("Nothing was sent", str(caught.exception))

    def test_a_splitter_that_cannot_cut_any_finer_stops_with_a_clear_message(self):
        whole = self.file("whole.xlsx", 3000)
        splitter, _ = self.rows_splitter(1)
        with self.assertRaises(JobUserError) as caught:
            attachment_fit.fit_together([whole], splitters={whole: splitter})
        self.assertIn("Nothing was sent", str(caught.exception))

    def test_other_files_in_the_mail_are_kept_beside_the_split_ones(self):
        big, small = self.file("big.xlsx", 3000), self.file("small.xlsx", 100)
        splitter, _ = self.rows_splitter(300)
        groups, _, finals = attachment_fit.fit_together([big, small], splitters={big: splitter})
        names = [p.name for p in finals]
        self.assertIn("small.xlsx", names)
        self.assertEqual(sorted(Path(p).name for g in groups for p in g), sorted(names))


if __name__ == "__main__":
    unittest.main()
