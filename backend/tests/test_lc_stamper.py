"""RDC LC Stamper: reading the list, finding and judging the invoice pages, stamping, the report, OCR tiers."""

import datetime as dt
import json
import tempfile
import threading
import unittest
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl
import pymupdf as fitz

from app.services.lc_stamper import amounts, analysis, decide, engine, ocr, stamping, targets
from app.services.lc_stamper.models import PageResult, Rules, StampSpec, Target
from tests.lc_stamper_helpers import invoice_text, make_pdf

D = dt.date


def _run(documents, tgts, rules=None, mode="stamp", stamp=None, output="per_document", cfg_ocr=None, workers=2):
    out = Path(tempfile.mkdtemp())
    cfg = engine.BatchConfig(
        [engine.Document(name, str(path)) for name, path in documents], str(out / "out"), tgts, rules or Rules(),
        mode, stamp or StampSpec(text="PAID"), output, cfg_ocr or ocr.OcrConfig())
    messages = []
    with ProcessPoolExecutor(workers, initializer=analysis.init_worker) as pool:
        result = engine.run_batch(cfg, pool, lambda f, m: messages.append((f, m)))
    return result, messages


def _text_of(path):
    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


class AmountTests(unittest.TestCase):
    def test_indian_and_plain_numbers(self):
        self.assertEqual(amounts.parse_amount("₹ 12,34,567.80"), 1234567.8)
        self.assertEqual(amounts.parse_amount("Rs.500/-"), 500.0)
        self.assertIsNone(amounts.parse_amount("abc"))
        self.assertEqual(amounts.numbers_in("Total 1,18,000.00 and 5"), [118000.0, 5.0])

    def test_the_labelled_total_beats_a_tax_line(self):
        text = "IGST 18%  1,800.00\nGrand Total  11,800.00\n"
        self.assertEqual(amounts.labelled_total(text), 11800.0)
        self.assertIsNone(amounts.labelled_total("nothing here"))

    def test_tolerance(self):
        self.assertTrue(amounts.amounts_match(100.0, [100.4], 0.5))
        self.assertFalse(amounts.amounts_match(100.0, [101.0], 0.5))


class ListTests(unittest.TestCase):
    def test_columns_are_found_by_name_and_numbers_cleaned(self):
        import pandas as pd
        df = pd.DataFrame({"LC No": ["LC1", "LC2", "LC1"], "Invoice Number": [123.0, "INV-9", 123.0],
                           "Invoice Date": ["15-01-2026", dt.datetime(2026, 2, 3), "15-01-2026"],
                           "Amount": ["1,000.50", 2000, "1,000.50"]})
        info = targets.targets_from_frame(df)
        self.assertEqual((info.rows, info.duplicates), (3, 1))
        self.assertEqual([t.invoice for t in info.targets], ["123", "INV-9"])
        self.assertEqual(info.targets[0].date, D(2026, 1, 15))
        self.assertEqual(info.targets[0].amount, 1000.5)
        self.assertEqual(info.targets[1].extra, {"LC No": "LC2"})
        self.assertTrue(info.has_dates and info.has_amounts)

    def test_stray_spaces_in_a_header_do_not_end_up_in_the_placeholder(self):
        import pandas as pd
        df = pd.DataFrame({"Invoice No": ["A1"], "Co  Code ": ["07"]})
        info = targets.targets_from_frame(df)
        self.assertEqual(info.columns, ["Co Code"])
        self.assertEqual(targets.stamp_text("{Co Code}", info.targets[0], 1), "07")

    def test_a_list_of_numbers_only(self):
        info = targets.parse_pasted("A1, A2\nA1;A3")
        self.assertEqual([t.invoice for t in info.targets], ["A1", "A2", "A3"])
        self.assertEqual(info.duplicates, 1)
        self.assertFalse(info.has_dates)

    def test_the_stamp_text_takes_the_invoice_values(self):
        target = Target("INV-1", D(2026, 1, 5), 1234567.5, {"LC No": "LC9"})
        text = targets.stamp_text("{invoice} {date} Rs.{amount} {LC No} {nope} p{page}", target, 3)
        self.assertEqual(text, "INV-1 05-Jan-2026 Rs.12,34,567.50 LC9 {nope} p3")

    def test_templates_keep_dates_as_dates(self):
        path = Path(tempfile.mkdtemp()) / "t.xlsx"
        targets.create_template(str(path), "full")
        ws = openpyxl.load_workbook(path)["Invoices"]
        self.assertEqual([c.value for c in ws[1]], ["Invoice No", "Invoice Date", "Amount"])
        self.assertIsInstance(ws["B2"].value, dt.datetime)
        targets.create_template(str(path), "filter")
        self.assertEqual([c.value for c in openpyxl.load_workbook(path)["Invoices"][1]], ["Invoice No"])


class MatchingTests(unittest.TestCase):
    def test_only_whole_numbers_match(self):
        self.assertIsNone(ocr.match_exact(["1001"], "ref 10012 and 21001"))
        self.assertEqual(ocr.match_exact(["1001", "INV-1001"], "No: INV-1001 date"), "INV-1001")
        self.assertEqual(ocr.match_exact(["7100000222"], "Invoice No.:7100000222Date"), "7100000222")

    def test_ocr_confusions_are_tolerated_but_neighbours_stay_apart(self):
        keys = ["RDC7100000222", "RDC7100000223"]
        self.assertEqual(ocr.match_ocr_text(keys, "Invoice RDC7l00000222"), "RDC7100000222")
        self.assertIsNone(ocr.match_ocr_text(["ABC12345"], "ABC12399"))

    def test_a_rich_text_layer_is_never_ocred_in_auto_mode(self):
        doc = fitz.open()
        page = doc.new_page()
        self.assertFalse(ocr.page_needs_ocr("word " * 400, page, "auto"))
        self.assertTrue(ocr.page_needs_ocr("word " * 400, page, "always"))
        self.assertFalse(ocr.page_needs_ocr("", page, "off"))


class DecisionTests(unittest.TestCase):
    def _results(self, *items):
        return [PageResult(0, i, key, "MATCH", pdf_date, amts, total) for i, (key, pdf_date, amts, total) in enumerate(items)]

    def test_exclude_or_flag_by_the_rules(self):
        results = self._results(("A", D(2026, 1, 2), (100.0,), 100.0), ("B", D(2026, 1, 2), (50.0,), 50.0))
        tgts = [Target("A", D(2026, 1, 3), 100.0), Target("B", D(2026, 1, 2), 100.0)]
        flagged = decide.decide(results, tgts, Rules(), ["d.pdf"])
        self.assertEqual(len(flagged.keep), 2)
        self.assertEqual([r.status for r in flagged.rows], ["⚠ Kept (Date Mismatch)", "⚠ Kept (Amount Mismatch)"])
        strict = decide.decide(results, tgts, Rules(match_dates=True, match_amounts=True), ["d.pdf"])
        self.assertEqual(strict.keep, [])
        self.assertEqual([r.status for r in strict.rows], ["✗ Date Mismatch", "✗ Amount Mismatch"])

    def test_a_row_that_fits_wins_and_a_second_page_is_a_duplicate(self):
        results = self._results(("A", D(2026, 1, 9), (), None), ("A", D(2026, 1, 3), (), None), ("A", D(2026, 1, 3), (), None))
        tgts = [Target("A", D(2026, 1, 3)), Target("A", D(2026, 1, 9))]
        out = decide.decide(results, tgts, Rules(), ["d.pdf"])
        self.assertEqual([r.status for r in out.rows], ["✓ Verified", "✓ Verified", "⚠ Duplicate — Skipped"])
        self.assertEqual(len(out.keep), 2)
        again = decide.decide(results, tgts, Rules(keep_duplicates=True), ["d.pdf"])
        self.assertEqual(len(again.keep), 3)

    def test_a_rejected_page_does_not_block_a_later_good_page(self):
        results = self._results(("A", D(2026, 1, 9), (), None), ("A", D(2026, 1, 3), (), None))
        out = decide.decide(results, [Target("A", D(2026, 1, 3))], Rules(match_dates=True), ["d.pdf"])
        self.assertEqual([r.status for r in out.rows], ["✗ Date Mismatch", "✓ Verified"])
        self.assertEqual([k[1] for k in out.keep], [1])

    def test_unknown_values_are_partial_and_missing_invoices_reported(self):
        out = decide.decide(self._results(("A", None, (), None)), [Target("A", D(2026, 1, 3), 10.0), Target("Z")], Rules(), ["d"])
        self.assertEqual([r.status for r in out.rows], ["⚠ Partial (Date & Amount?)", "✗ Not Found in PDF"])
        self.assertEqual(out.counts["not_found"], 1)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.a = make_pdf(self.tmp / "a.pdf", [invoice_text("INV-1001"), None, invoice_text("INV-1002", date="20-01-2026"),
                                               invoice_text("INV-9999")])
        self.b = make_pdf(self.tmp / "b.pdf", [invoice_text("INV-1001"), invoice_text("INV-1003", total="500.00")])
        self.tgts = [Target("INV-1001", D(2026, 1, 15), 11800.0), Target("INV-1002", D(2026, 1, 20), 11800.0),
                     Target("INV-1003", D(2026, 1, 15), 11800.0), Target("INV-0000")]

    def test_documents_are_read_and_stamped_side_by_side_with_the_invoice_values(self):
        result, messages = _run([("a.pdf", self.a), ("b.pdf", self.b)], self.tgts,
                                stamp=StampSpec(text="PAID {invoice} {date}", rel_x=0.5, rel_y=0.9))
        self.assertEqual(result["status"], "ok")
        self.assertEqual([(o["name"], o["pages"]) for o in result["outputs"]], [("a_stamped.pdf", 2), ("b_stamped.pdf", 1)])
        a_text = _text_of(result["outputs"][0]["path"])
        self.assertIn("PAID INV-1001 15-Jan-2026", a_text[0])
        self.assertIn("PAID INV-1002 20-Jan-2026", a_text[1])
        self.assertNotIn("INV-9999", "".join(a_text))
        statuses = {(r["invoice"], r["document"]): r["status"] for r in result["rows"]}
        self.assertEqual(statuses[("INV-1001", "b.pdf")], "⚠ Duplicate — Skipped")
        self.assertEqual(statuses[("INV-1003", "b.pdf")], "⚠ Kept (Amount Mismatch)")
        self.assertEqual(statuses[("INV-0000", "")], "✗ Not Found in PDF")
        self.assertEqual(result["counts"]["pages_by_text"], 5)
        self.assertEqual(result["counts"]["pages_by_online_ocr"] + result["counts"]["pages_by_offline_ocr"], 0)
        self.assertEqual(messages[-1][0], 1.0)

    def test_one_combined_pdf(self):
        result, _ = _run([("a.pdf", self.a), ("b.pdf", self.b)], self.tgts, output="combined")
        self.assertEqual(len(result["outputs"]), 1)
        self.assertEqual(result["outputs"][0]["pages"], 3)
        self.assertEqual([r["out_page"] for r in result["rows"] if r["out_page"]], [1, 2, 3])

    def test_the_rules_leave_pages_out(self):
        result, _ = _run([("a.pdf", self.a), ("b.pdf", self.b)], self.tgts, Rules(match_amounts=True))
        self.assertEqual(sum(o["pages"] for o in result["outputs"]), 2)
        self.assertEqual(result["counts"]["mismatches"], 1)

    def test_filter_only_keeps_the_pages_as_they_are_and_reports_found_or_not(self):
        result, _ = _run([("a.pdf", self.a)], [Target("INV-1001"), Target("INV-1002"), Target("INV-0000")], mode="filter")
        self.assertEqual(result["outputs"][0]["name"], "a_filtered.pdf")
        self.assertNotIn("PAID", "".join(_text_of(result["outputs"][0]["path"])))
        ws = openpyxl.load_workbook(result["report_path"])["Report"]
        header = [c.value for c in ws[1]]
        self.assertEqual(header[:2], ["Invoice No", "Found"])
        self.assertEqual([row[1].value for row in ws.iter_rows(min_row=2)], ["Yes", "Yes", "No"])

    def test_no_list_keeps_every_page_with_something_on_it(self):
        result, _ = _run([("a.pdf", self.a)], [], stamp=StampSpec(text="SEEN"))
        self.assertEqual(result["outputs"][0]["pages"], 3)
        self.assertIsNone(result["report_path"])

    def test_the_report_holds_real_dates_and_numbers(self):
        result, _ = _run([("a.pdf", self.a)], self.tgts)
        ws = openpyxl.load_workbook(result["report_path"])["Report"]
        header = [c.value for c in ws[1]]
        row = [c for c in ws[2]]
        self.assertIsInstance(row[header.index("Excel Date")].value, dt.datetime)
        self.assertIsInstance(row[header.index("PDF Date")].value, dt.datetime)
        self.assertIn("mmm", row[header.index("PDF Date")].number_format.lower())
        self.assertIsInstance(row[header.index("Excel Amount")].value, (int, float))
        self.assertIn("Summary", openpyxl.load_workbook(result["report_path"]).sheetnames)

    def test_an_invoice_only_list_has_no_date_or_amount_columns(self):
        result, _ = _run([("a.pdf", self.a)], [Target("INV-1001")])
        header = [c.value for c in openpyxl.load_workbook(result["report_path"])["Report"][1]]
        self.assertNotIn("Excel Date", header)
        self.assertNotIn("Excel Amount", header)

    def test_cancelling_stops_the_batch(self):
        out = Path(tempfile.mkdtemp())
        cfg = engine.BatchConfig([engine.Document("a.pdf", str(self.a))], str(out), self.tgts)
        with ProcessPoolExecutor(1, initializer=analysis.init_worker) as pool:
            with self.assertRaises(engine.Cancelled):
                engine.run_batch(cfg, pool, lambda f, m: None, lambda: True)

    def test_a_broken_pdf_is_explained(self):
        bad = self.tmp / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        with self.assertRaises(engine.BatchError) as caught:
            _run([("bad.pdf", bad)], self.tgts)
        self.assertIn("bad.pdf", str(caught.exception))

    def test_a_stamp_off_the_page_is_reported(self):
        result, _ = _run([("a.pdf", self.a)], self.tgts, stamp=StampSpec(text="WIDE " * 40, rel_x=0.8))
        self.assertGreater(result["counts"]["stamp_overflow_pages"], 0)
        self.assertTrue(any("runs off the page" in w for w in result["warnings"]))


class StampPositionTests(unittest.TestCase):
    """The stamp in the written PDF lands where the preview sprite says (the stamp is drawn by one code path)."""

    def _compare(self, rotation, crop=None, zoom=2.0):
        import numpy as np
        tmp = Path(tempfile.mkdtemp())
        doc = fitz.open()
        page = doc.new_page(width=420, height=300)
        if crop:
            page.set_cropbox(fitz.Rect(*crop))
        page.set_rotation(rotation)
        src = tmp / "blank.pdf"
        doc.save(str(src))
        doc.close()
        spec = StampSpec("APPROVED 12345", 14, (0, 0, 0), 0.31, 0.62, True)
        out = tmp / "out.pdf"
        engine.build_part(str(src), [0], [spec.text], {
            "text": spec.text, "font_size": spec.font_size, "color": spec.color, "rel_x": spec.rel_x,
            "rel_y": spec.rel_y, "bold": spec.bold}, str(out))
        with fitz.open(str(out)) as stamped:
            pix = stamped[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
            real = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(float)
            width, height = stamped[0].rect.width, stamped[0].rect.height
        sprite = stamping.render_stamp_sprite(spec, zoom, (spec.rel_x * width * zoom, spec.rel_y * height * zoom))
        rgba = np.frombuffer(sprite.data, np.uint8).reshape(sprite.height, sprite.width, 4).astype(float)
        expected = np.full_like(real, 255.0)
        y0, x0 = sprite.y, sprite.x
        for dy in range(sprite.height):
            for dx in range(sprite.width):
                y, x = y0 + dy, x0 + dx
                if 0 <= y < expected.shape[0] and 0 <= x < expected.shape[1]:
                    alpha = rgba[dy, dx, 3] / 255.0
                    expected[y, x] = 255.0 * (1 - alpha) + rgba[dy, dx, :3].mean() * alpha
        self.assertGreater((real < 128).sum(), 50)                           # something was drawn
        self.assertLess(np.abs(real - expected).mean(), 1.5)                 # and it is where the sprite is
        return np.abs(real - expected).max()

    def test_every_rotation(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                self._compare(rotation)

    def test_a_crop_box_that_does_not_start_at_the_origin(self):
        self._compare(0, crop=(30, 20, 400, 280))
        self._compare(90, crop=(30, 20, 400, 280))


class _FakeOcrServer:
    """A stand-in for the online OCR service: answers with the invoice text, or with an error."""

    def __init__(self, text=None, status=200, error=None):
        outer = self
        outer.calls = 0

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.calls += 1
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if error:
                    body = json.dumps({"IsErroredOnProcessing": True, "ErrorMessage": [error]}).encode()
                else:
                    body = json.dumps({"ParsedResults": [{"ParsedText": text or ""}],
                                       "IsErroredOnProcessing": False}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/parse"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _scanned_pdf(tmp, pages):
    source = make_pdf(tmp / "text.pdf", pages)
    out = fitz.open()
    with fitz.open(str(source)) as doc:
        for page in doc:
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=page.get_pixmap(dpi=130))
    path = tmp / "scan.pdf"
    out.save(str(path))
    return path


class OcrTierTests(unittest.TestCase):
    """Text layer first, then online OCR, then offline OCR; OCR only for the pages that need it."""

    def setUp(self):
        ocr._online_state.update(fails=0, off_until=0.0)
        self.tmp = Path(tempfile.mkdtemp())
        self.scan = _scanned_pdf(self.tmp, [invoice_text("INV-1001")])
        self.text_pdf = make_pdf(self.tmp / "t.pdf", [invoice_text("INV-1001")])
        self.tgts = [Target("INV-1001", D(2026, 1, 15))]

    def test_online_ocr_reads_the_scan_when_it_is_set_up(self):
        server = _FakeOcrServer(invoice_text("INV-1001"))
        try:
            cfg = ocr.OcrConfig(online_url=server.url, online_key="k")
            result, _ = _run([("scan.pdf", self.scan)], self.tgts, cfg_ocr=cfg)
        finally:
            server.close()
        self.assertEqual(result["rows"][0]["read_by"], "online")
        self.assertEqual(result["rows"][0]["status"], "✓ Verified")
        self.assertEqual(result["counts"]["pages_by_online_ocr"], 1)

    @unittest.skipUnless(ocr.offline_available(), "offline OCR models are not installed")
    def test_offline_ocr_takes_over_when_the_online_service_fails(self):
        server = _FakeOcrServer(status=500)
        try:
            cfg = ocr.OcrConfig(online_url=server.url, online_key="k")
            result, _ = _run([("scan.pdf", self.scan)], self.tgts, cfg_ocr=cfg)
        finally:
            server.close()
        self.assertEqual(result["rows"][0]["read_by"], "offline")
        self.assertEqual(result["rows"][0]["status"], "✓ Verified")

    @unittest.skipUnless(ocr.offline_available(), "offline OCR models are not installed")
    def test_a_used_up_free_quota_pauses_the_online_tier_for_every_worker_and_offline_takes_over(self):
        server = _FakeOcrServer(error="You may only perform this action upto maximum 500 times for 24 hours.")
        state = self.tmp / "paused.json"
        try:
            cfg = ocr.OcrConfig(online_url=server.url, online_key="k", state_file=str(state))
            result, _ = _run([("scan.pdf", self.scan)], self.tgts, cfg_ocr=cfg)
            self.assertEqual(result["rows"][0]["read_by"], "offline")
            self.assertEqual(result["rows"][0]["status"], "✓ Verified")
            self.assertTrue(state.exists())
            self.assertIsNotNone(ocr.shared_pause(cfg))
            calls = server.calls
            # a worker that has never seen the error (fresh state) still does not call the service
            ocr._online_state.update(fails=0, off_until=0.0)
            self.assertFalse(ocr.online_usable(cfg))
            _run([("scan.pdf", self.scan)], self.tgts, cfg_ocr=cfg)
            self.assertEqual(server.calls, calls)
        finally:
            server.close()

    def test_an_error_about_this_one_image_does_not_switch_the_online_tier_off(self):
        server = _FakeOcrServer(error="File size exceeds the maximum permissible limit")
        state = self.tmp / "paused.json"
        try:
            cfg = ocr.OcrConfig(online_url=server.url, online_key="k", state_file=str(state))
            from PIL import Image
            with self.assertRaises(ocr.OnlineUnavailable):
                ocr.read_online(Image.new("L", (50, 50), 255), cfg)
        finally:
            server.close()
        self.assertFalse(state.exists())
        self.assertFalse(ocr.is_quota_message("File size exceeds the maximum permissible limit"))
        self.assertTrue(ocr.is_quota_message("You may only perform this action upto maximum 500 times for 24 hours"))

    @unittest.skipUnless(ocr.offline_available(), "offline OCR models are not installed")
    def test_offline_ocr_alone_reads_a_scan_and_a_text_pdf_needs_no_ocr(self):
        result, _ = _run([("scan.pdf", self.scan), ("t.pdf", self.text_pdf)], self.tgts,
                         Rules(online_ocr=False))
        by_doc = {r["document"]: r for r in result["rows"] if r["document"]}
        self.assertEqual(by_doc["scan.pdf"]["read_by"], "offline")
        self.assertEqual(result["counts"]["pages_by_offline_ocr"], 1)

    def test_ocr_off_leaves_a_scan_unmatched(self):
        server = _FakeOcrServer(invoice_text("INV-1001"))
        try:
            cfg = ocr.OcrConfig(online_url=server.url, online_key="k")
            result, _ = _run([("scan.pdf", self.scan)], self.tgts, Rules(ocr_mode="off"), cfg_ocr=cfg)
        finally:
            server.close()
        self.assertEqual(server.calls, 0)
        self.assertEqual(result["rows"][0]["status"], "✗ Not Found in PDF")

    def test_a_text_pdf_never_calls_the_online_service(self):
        server = _FakeOcrServer(invoice_text("INV-1001"))
        try:
            cfg = ocr.OcrConfig(online_url=server.url, online_key="k")
            _run([("t.pdf", self.text_pdf)], self.tgts, cfg_ocr=cfg)
        finally:
            server.close()
        self.assertEqual(server.calls, 0)


class RouterTests(unittest.TestCase):
    """The router functions the page calls, with a staged document and list."""

    def setUp(self):
        from app.routers import lc_stamper as router
        self.router = router
        self.tmp = Path(tempfile.mkdtemp())
        self.root_patch = patch.object(router, "_ROOT", self.tmp / "stage")
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.user = SimpleNamespace(id=424242)
        self.pdf = make_pdf(self.tmp / "a.pdf", [invoice_text("INV-1001"), invoice_text("INV-1002")])

    def _stage(self):
        user_dir = self.router._user_dir(self.user)
        doc_id = "a" * 32
        (user_dir / f"{doc_id}.pdf").write_bytes(self.pdf.read_bytes())
        (user_dir / f"{doc_id}.json").write_text(json.dumps({"id": doc_id, "name": "a.pdf", "pages": 2, "size": 1,
                                                             "text_pages": 2, "sampled": 2}))
        info = targets.parse_pasted("INV-1001\nINV-1002")
        list_id = self.router._store_list(self.user, info)["id"]
        return doc_id, list_id

    def test_the_job_runs_through_the_pool_and_the_files_are_served_only_from_its_folder(self):
        doc_id, list_id = self._stage()
        with ProcessPoolExecutor(2, initializer=analysis.init_worker) as pool, \
                patch.object(self.router.jobs, "cpu_pool", return_value=pool), \
                patch.object(self.router, "SCRATCH_DIR", self.tmp / "scratch"):
            (self.tmp / "scratch").mkdir()
            result = self.router._run_job(self.user.id, [[doc_id, "a.pdf"]], list_id, "stamp", "per_document",
                                          self.router.RulesIn().model_dump(),
                                          self.router.StampIn(text="OK {invoice}").model_dump())
        self.assertEqual(result["outputs"][0]["pages"], 2)
        self.assertEqual(self.router._inside_outputs(result, result["outputs"][0]["path"]).name, "a_stamped.pdf")
        with self.assertRaises(Exception):
            self.router._inside_outputs(result, str(self.pdf))               # a file outside the run's folder
        self.assertTrue(Path(result["report_path"]).is_file())

    def test_a_document_that_is_gone_is_explained(self):
        from app.jobs import JobUserError
        with self.assertRaises(JobUserError):
            self.router._run_job(self.user.id, [["b" * 32, "gone.pdf"]], None, "stamp", "per_document",
                                 self.router.RulesIn().model_dump(), self.router.StampIn(text="x").model_dump())

    def test_the_stamp_preview_fills_the_invoice_values(self):
        _, list_id = self._stage()
        body = self.router.StampPreviewIn(stamp=self.router.StampIn(text="Paid {invoice}"), list_id=list_id)
        reply = self.router.stamp_preview(body, user=self.user)
        self.assertEqual(reply["text"], "Paid INV-1001")
        self.assertGreater(reply["box_w"], 10)

    def test_run_refuses_what_cannot_work(self):
        from fastapi import HTTPException
        doc_id, list_id = self._stage()
        for body in (
            self.router.RunIn(document_ids=[doc_id], mode="filter"),
            self.router.RunIn(document_ids=[doc_id], list_id=list_id, stamp=self.router.StampIn(text=" ")),
            self.router.RunIn(document_ids=[doc_id], list_id=list_id, stamp=self.router.StampIn(text="x"),
                              rules=self.router.RulesIn(match_dates=True)),
        ):
            with self.subTest(body=body.mode), self.assertRaises(HTTPException) as caught:
                self.router.run(body, user=self.user)
            self.assertEqual(caught.exception.status_code, 400)

    def test_a_document_id_is_checked(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self.router._check_id("../../etc/passwd")

    def test_the_page_image_is_a_png(self):
        doc_id, _ = self._stage()
        reply = self.router.page_image(doc_id, 1, 1.0, user=self.user)
        self.assertTrue(reply.body.startswith(b"\x89PNG"))
        self.assertEqual(reply.headers["x-page-width-pt"], "595.00")


if __name__ == "__main__":
    unittest.main()
