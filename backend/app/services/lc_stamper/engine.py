"""Running a batch: scan every page of every document in parallel, decide, stamp each document in parallel, report.

``run_batch`` is called from a background job. It never touches the web framework or the database: the process pool,
the progress callback and the cancel check are handed in, so it is tested with a plain ``ProcessPoolExecutor``.

How the work is spread (all of it in the pool's worker processes, one page range or one document per task):

* scan   - every document is cut into chunks of pages; chunks of all documents run side by side. A page is read from
           its text layer; OCR runs only for the pages that need it (see ``analysis``);
* decide - the batch's pages are judged together, in upload order (``decide``);
* stamp  - one task per document, all documents at once. The output is one PDF per document, or one PDF for the batch
           (the per-document parts are then joined).
"""

from __future__ import annotations

import concurrent.futures as cf
from concurrent.futures.process import BrokenProcessPool
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .decide import Decision, decide
from .models import (MODE_FILTER, MODE_STAMP, OUTPUT_COMBINED, PageResult, Rules, StampSpec, Target)
from .ocr import OcrConfig
from .report import write_report
from .targets import stamp_text

_ROW_LIMIT = 3000                  # report rows handed back to the page (the workbook has them all)


class BatchError(Exception):
    """A problem the person can fix; the message is shown as it is."""


class Cancelled(Exception):
    pass


@dataclass
class Document:
    name: str
    path: str


@dataclass
class BatchConfig:
    documents: list
    out_dir: str
    targets: list = field(default_factory=list)
    rules: Rules = field(default_factory=Rules)
    mode: str = MODE_STAMP
    stamp: StampSpec = field(default_factory=StampSpec)
    output: str = "per_document"
    ocr: OcrConfig = field(default_factory=OcrConfig)


# ── helpers ────────────────────────────────────────────────────────────────────────────────────────────────────────

def pdf_pages(path: str, name: str = "") -> int:
    """The page count of a PDF; BatchError with a plain message when it cannot be used."""
    import pymupdf as fitz
    label = name or os.path.basename(path)
    try:
        with fitz.open(path) as doc:
            if doc.needs_pass:
                raise BatchError(f"\"{label}\" is password-protected. Remove the password and upload it again.")
            return doc.page_count
    except BatchError:
        raise
    except Exception as exc:
        raise BatchError(f"\"{label}\" is not a readable PDF ({exc.__class__.__name__}).") from exc


def chunk_size(pages: int, workers: int, may_ocr: bool) -> int:
    low, high = (3, 8) if may_ocr else (10, 40)
    return max(low, min(high, math.ceil(pages / max(1, workers * 5))))


def _safe_name(name: str) -> str:
    stem = Path(name).stem or "document"
    return "".join(ch if ch.isalnum() or ch in " ._-()" else "_" for ch in stem).strip(" .") or "document"


def _workers(pool) -> int:
    return max(1, getattr(pool, "_max_workers", 2) or 2)


def _wait(futures: set, cancelled: Callable[[], bool], on_done: Callable[[cf.Future], None]) -> None:
    pending = set(futures)
    while pending:
        if cancelled():
            for future in pending:
                future.cancel()
            raise Cancelled()
        done, pending = cf.wait(pending, timeout=0.25, return_when=cf.FIRST_COMPLETED)
        for future in done:
            on_done(future)


# ── the stamping task (runs in a worker process) ───────────────────────────────────────────────────────────────────

def build_part(src_path: str, pages: list, texts: list, spec: dict, out_path: str) -> tuple:
    """Write ``pages`` (0-based, in order) of ``src_path`` to ``out_path``, each stamped with its own text
    (``texts[i]`` is None / "" for no stamp). Returns (pages written, pages whose stamp runs off the page)."""
    import pymupdf as fitz
    from . import stamping
    doc = fitz.open(src_path)
    try:
        doc.select(list(pages))          # select() keeps shared fonts and images shared: a small output
        overflow = 0
        base = StampSpec(**spec)
        for i in range(doc.page_count):
            text = texts[i]
            if not text:
                continue
            page = doc[i]
            one = StampSpec(text, base.font_size, tuple(base.color), base.rel_x, base.rel_y, base.bold)
            rect = stamping.stamp_page(page, one)
            bounds = page.rect
            if rect.x1 > bounds.x1 + 0.5 or rect.y1 > bounds.y1 + 0.5 or rect.x0 < -0.5 or rect.y0 < -0.5:
                overflow += 1
        doc.save(out_path, garbage=1, deflate=True)
        return doc.page_count, overflow
    finally:
        doc.close()


def _join(parts: list, out_path: str) -> int:
    import pymupdf as fitz
    merged = fitz.open()
    try:
        for part in parts:
            with fitz.open(part) as piece:
                merged.insert_pdf(piece)
        merged.save(out_path, garbage=1, deflate=True)
        return merged.page_count
    finally:
        merged.close()


# ── the batch ──────────────────────────────────────────────────────────────────────────────────────────────────────

def run_batch(cfg: BatchConfig, pool, progress: Callable[[float, str], None],
              cancelled: Callable[[], bool] = lambda: False) -> dict:
    from .analysis import scan_chunk

    started = time.perf_counter()
    if not cfg.documents:
        raise BatchError("Upload at least one PDF.")
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    progress(0.01, "Reading the PDFs…")
    sizes = [pdf_pages(d.path, d.name) for d in cfg.documents]
    total = sum(sizes)
    if total == 0:
        raise BatchError("The uploaded PDFs have no pages.")
    names = [d.name for d in cfg.documents]

    targets = list(cfg.targets)
    keys = sorted({t.invoice for t in targets}, key=len, reverse=True) or None
    need_date = any(t.date for t in targets)
    need_amount = any(t.amount is not None for t in targets)
    rules = cfg.rules
    rules_dict = {"skip_blank": rules.skip_blank, "ocr_mode": rules.ocr_mode, "online_ocr": rules.online_ocr}
    may_ocr = rules.ocr_mode != "off"

    # ── scan ───────────────────────────────────────────────────────────────────────────────────────────────────────
    size = chunk_size(total, _workers(pool), may_ocr)
    futures: dict = {}
    for doc_index, document in enumerate(cfg.documents):
        for start in range(0, sizes[doc_index], size):
            pages = list(range(start, min(sizes[doc_index], start + size)))
            future = pool.submit(scan_chunk, doc_index, document.path, pages, keys, rules_dict, cfg.ocr,
                                 need_date, need_amount)
            futures[future] = (doc_index, pages)
    results: list = []
    scanned = 0

    def collect(future):
        nonlocal scanned
        doc_index, pages = futures[future]
        try:
            results.extend(future.result())
        except cf.CancelledError:
            raise Cancelled()
        except BrokenProcessPool as exc:
            raise BatchError("A background worker stopped unexpectedly while reading the pages. "
                             "Please run it again.") from exc
        except Exception as exc:
            results.extend(PageResult(doc_index, p, None, f"ERROR: {exc}") for p in pages)
        scanned += len(pages)
        progress(0.04 + 0.56 * scanned / total, f"Reading pages… {scanned:,} of {total:,}")

    progress(0.04, f"Reading {total:,} page{'s' if total != 1 else ''}…")
    _wait(set(futures), cancelled, collect)

    # ── decide ─────────────────────────────────────────────────────────────────────────────────────────────────────
    progress(0.62, "Checking the invoices…")
    decision: Decision = decide(results, targets, rules, names)
    counts = dict(decision.counts)
    counts.update(pages=total, documents=len(cfg.documents))
    warnings: list = []
    if counts["unreadable"]:
        warnings.append(f"{counts['unreadable']} page(s) could not be read and were skipped.")

    # ── output ─────────────────────────────────────────────────────────────────────────────────────────────────────
    outputs: list = []
    kept_by_doc: dict = {}
    for entry in decision.keep:
        kept_by_doc.setdefault(entry[0], []).append(entry)
    stamping_on = cfg.mode == MODE_STAMP and bool((cfg.stamp.text or "").strip())
    spec = {"text": cfg.stamp.text, "font_size": cfg.stamp.font_size, "color": tuple(cfg.stamp.color),
            "rel_x": cfg.stamp.rel_x, "rel_y": cfg.stamp.rel_y, "bold": cfg.stamp.bold}
    overflow = 0
    suffix = "stamped" if stamping_on else "filtered"
    if kept_by_doc:
        progress(0.65, "Stamping the pages…" if stamping_on else "Collecting the pages…")
        tag = uuid.uuid4().hex[:6]
        parts: dict = {}
        build_futures: dict = {}
        for doc_index, entries in sorted(kept_by_doc.items()):
            texts = [stamp_text(cfg.stamp.text, target, page + 1, names[doc_index]) if stamping_on else None
                     for _, page, target, _ in entries]
            part_path = str(out_dir / f".part_{tag}_{doc_index}.pdf")
            future = pool.submit(build_part, cfg.documents[doc_index].path, [e[1] for e in entries], texts, spec,
                                 part_path)
            build_futures[future] = (doc_index, part_path)
        built = 0

        def collect_part(future):
            nonlocal built, overflow
            doc_index, part_path = build_futures[future]
            try:
                _, off = future.result()
            except cf.CancelledError:
                raise Cancelled()
            except Exception as exc:
                raise BatchError(f"\"{names[doc_index]}\" could not be written: {exc}") from exc
            overflow += off
            parts[doc_index] = part_path
            built += 1
            progress(0.65 + 0.27 * built / len(build_futures), f"Stamped {built} of {len(build_futures)} document(s)…"
                     if stamping_on else f"Collected {built} of {len(build_futures)} document(s)…")

        try:
            _wait(set(build_futures), cancelled, collect_part)
            if cfg.output == OUTPUT_COMBINED and len(parts) > 0:
                name = f"LC_{suffix.title()}_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
                path = str(out_dir / name)
                pages_written = _join([parts[i] for i in sorted(parts)], path)
                outputs.append({"name": name, "path": path, "pages": pages_written, "document": "All documents"})
                number = 0
                for doc_index in sorted(kept_by_doc):
                    for _, _, _, row in kept_by_doc[doc_index]:
                        number += 1
                        if row is not None:
                            row.out_page = number
            else:
                used = set()
                for doc_index in sorted(parts):
                    base = f"{_safe_name(names[doc_index])}_{suffix}"
                    name, n = f"{base}.pdf", 1
                    while name.lower() in used:
                        n += 1
                        name = f"{base}_{n}.pdf"
                    used.add(name.lower())
                    path = str(out_dir / name)
                    os.replace(parts[doc_index], path)
                    parts[doc_index] = None
                    outputs.append({"name": name, "path": path, "pages": len(kept_by_doc[doc_index]),
                                    "document": names[doc_index]})
                    for number, (_, _, _, row) in enumerate(kept_by_doc[doc_index], start=1):
                        if row is not None:
                            row.out_page = number
        finally:
            for leftover in parts.values():
                if leftover:
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
    if overflow:
        warnings.append(f"The stamp runs off the page on {overflow} page(s). Move it inside the page and run again.")
    counts["stamp_overflow_pages"] = overflow

    # ── report ─────────────────────────────────────────────────────────────────────────────────────────────────────
    report_path, report_name = None, None
    if targets and decision.rows:
        progress(0.94, "Writing the report…")
        report_name = f"LC_{'Verification' if cfg.mode == MODE_STAMP else 'Filter'}_Report_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
        report_path = str(out_dir / report_name)
        summary = [
            ["RDC LC Stamper — summary"], [],
            ["Documents", len(cfg.documents)], ["Pages read", total],
            ["Pages kept", counts["kept"]], ["Duplicates", counts["duplicates"]],
            ["Left out (date / amount mismatch)", counts["mismatches"]],
            ["Kept with a mismatch flagged", counts["flagged"]], ["Invoices not found", counts["not_found"]],
            ["Pages read from the PDF text", counts["pages_by_text"]],
            ["Pages read by online OCR", counts["pages_by_online_ocr"]],
            ["Pages read by offline OCR", counts["pages_by_offline_ocr"]],
        ]
        try:
            write_report(decision, report_path, cfg.mode, any(t.date for t in targets),
                         any(t.amount is not None for t in targets), summary)
        except Exception as exc:
            report_path = report_name = None
            warnings.append(f"The PDFs were saved, but the report could not be written ({exc}).")

    progress(1.0, "Done")
    rows = [{
        "invoice": r.invoice, "status": r.status, "document": r.document, "page": r.page, "out_page": r.out_page,
        "excel_date": r.target.date.isoformat() if r.target and r.target.date else None,
        "pdf_date": r.pdf_date.isoformat() if r.pdf_date else None, "date_match": r.date_match,
        "excel_amount": r.target.amount if r.target else None, "pdf_amount": r.pdf_amount,
        "amount_match": r.amount_match, "read_by": r.read_by,
    } for r in decision.rows[:_ROW_LIMIT]]
    return {
        "status": "ok" if outputs else "no_match",
        "mode": cfg.mode, "outputs": outputs, "report_path": report_path, "report_name": report_name,
        "counts": counts, "warnings": warnings, "rows": rows, "rows_total": len(decision.rows),
        "documents": [{"name": d.name, "pages": sizes[i], "kept": len(kept_by_doc.get(i, []))}
                      for i, d in enumerate(cfg.documents)],
        "elapsed_s": round(time.perf_counter() - started, 1),
    }
