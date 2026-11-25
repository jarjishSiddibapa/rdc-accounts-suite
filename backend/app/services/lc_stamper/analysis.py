"""Scanning the pages of one PDF (runs inside worker processes).

Every page is read the cheapest way that works:

1. the PDF's own text layer - no OCR at all (the normal case for invoices a system printed to PDF);
2. only when that layer cannot be trusted (a scan, garbled or outlined text) and the invoice number is not already
   found: OCR - the online service first (when it is set up and answers), the offline engine otherwise. The top of the
   page is read first (the invoice number and date are there); the whole page only when that was not enough.

Import-light on purpose; PyMuPDF and the OCR engines are imported inside the functions.
"""

from __future__ import annotations

import os
import sys

from . import amounts as amount_tools
from .dates import _extract_invoice_date_from_page
from .models import PageResult

_BLANK_MIN_CHARS = 5
ONLINE_PARALLEL = max(1, int(os.environ.get("LC_ONLINE_PARALLEL", "3")))   # online OCR calls in flight per worker (network wait, not CPU)


def init_worker() -> None:
    """Pool initializer: a polite background process (below-normal priority), single-threaded numeric libraries."""
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "1")
    try:
        if sys.platform == "win32":
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000)     # BELOW_NORMAL_PRIORITY_CLASS
        else:
            os.nice(5)
    except Exception:
        pass


def _scan_page(page, doc_index, page_index, keys, rules, cfg, need_date, need_amount,
               text=None, wants_ocr=None, prefetch=None) -> PageResult:
    """``text`` / ``wants_ocr`` / ``prefetch`` come from scan_chunk when it already looked at the page (and started the
    online OCR of the pages that need it, so the calls overlap); alone, the page is looked at here."""
    from . import ocr as ocr_tools

    if text is None:
        text = page.get_text()
    blank = len(text.strip()) < _BLANK_MIN_CHARS
    ocr_allowed = rules["ocr_mode"] != "off"
    if wants_ocr is None:
        wants_ocr = ocr_allowed and ocr_tools.page_needs_ocr(text, page, rules["ocr_mode"])

    if blank and rules["skip_blank"] and not wants_ocr:
        return PageResult(doc_index, page_index, None, "SKIPPED_BLANK")
    if not keys:                                                  # no list: every page that has something on it
        return PageResult(doc_index, page_index, "ALL", "MATCH")

    read_by = "text"
    ocr_text = ""
    key = ocr_tools.match_exact(keys, text)

    def read(top):
        nonlocal ocr_text, read_by
        started = prefetch.pop(page_index, None) if prefetch else None
        if started is not None and started[0] == top:
            try:
                got, how = started[1].result(), "online"
            except ocr_tools.OnlineUnavailable:                       # busy / quota used up: offline engine
                got, how = ocr_tools.read_page(page, cfg, False, top)
        else:
            got, how = ocr_tools.read_page(page, cfg, rules["online_ocr"], top)
        ocr_text = got if not ocr_text else ocr_text + "\n" + got
        read_by = how

    if key is None and wants_ocr:
        try:
            if need_amount:
                read(None)
            else:
                read(ocr_tools.HEADER_FRACTION)                     # the top of the page first
                found = ocr_tools.match_ocr_text(keys, ocr_text)
                if found is None:
                    ocr_text = ""
                    read(None)                                      # not there: the whole page
        except Exception as exc:
            return PageResult(doc_index, page_index, None, f"ERROR: {exc}", read_by=read_by)
        key = ocr_tools.match_ocr_text(keys, ocr_text)
        if key is None:
            if blank and rules["skip_blank"] and len(ocr_text.strip()) < _BLANK_MIN_CHARS:
                return PageResult(doc_index, page_index, None, "SKIPPED_BLANK", read_by=read_by)
            return PageResult(doc_index, page_index, None, "NO_MATCH", read_by=read_by)

    if key is None:
        return PageResult(doc_index, page_index, None, "NO_MATCH")

    combined = text if not ocr_text else text + "\n" + ocr_text
    pdf_date = _extract_invoice_date_from_page(combined) if need_date else None
    total, found_amounts = None, ()
    if need_amount:
        total, found_amounts = amount_tools.labelled_total(combined), amount_tools.page_amounts(combined)

    # the number came from the text layer but the date / amount are missing and the page is a scan under a thin
    # text layer: one OCR read of the page may still find them
    if (wants_ocr and not ocr_text and ((need_date and pdf_date is None) or (need_amount and not found_amounts))):
        try:
            read(None)
            combined = text + "\n" + ocr_text
            if need_date and pdf_date is None:
                pdf_date = _extract_invoice_date_from_page(combined)
            if need_amount:
                total, found_amounts = amount_tools.labelled_total(combined), amount_tools.page_amounts(combined)
        except Exception:
            pass                                                    # the invoice itself was found

    return PageResult(doc_index, page_index, key, "MATCH", pdf_date, found_amounts, total, read_by)


def scan_chunk(doc_index: int, path: str, pages: list, keys, rules: dict, cfg, need_date: bool,
               need_amount: bool) -> list:
    """Scan ``pages`` of one PDF. Opens the file once for the whole chunk. Never raises: a page that cannot be read
    comes back as an ERROR result."""
    import pymupdf as fitz
    try:
        doc = fitz.open(path)
    except Exception as exc:
        return [PageResult(doc_index, i, None, f"ERROR: cannot open the PDF: {exc}") for i in pages]
    out = []
    executor = None
    try:
        keys = tuple(keys) if keys else ()
        looked: dict = {}
        prefetch: dict = {}
        if keys and rules["online_ocr"] and rules["ocr_mode"] != "off":
            executor, looked, prefetch = _start_online(doc, pages, keys, rules, cfg, need_amount)
        for index in pages:
            try:
                text, wants = looked.get(index, (None, None))
                out.append(_scan_page(doc.load_page(index), doc_index, index, keys, rules, cfg, need_date, need_amount,
                                      text, wants, prefetch))
            except Exception as exc:
                out.append(PageResult(doc_index, index, None, f"ERROR: {exc}"))
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        doc.close()
    return out


def _start_online(doc, pages, keys, rules, cfg, need_amount):
    """Look at every page of the chunk once and send the pages that need OCR to the online service a few at a time,
    so a worker does not sit idle waiting for the network between pages. Pages are rendered here (PyMuPDF is not
    thread-safe); only the HTTP calls run in threads. Returns (executor | None, {page: (text, wants_ocr)},
    {page: (top fraction, future)}). When the online service is not usable nothing is started and every page falls
    through to the ordinary path."""
    from concurrent.futures import ThreadPoolExecutor
    from . import ocr as ocr_tools

    looked: dict = {}
    prefetch: dict = {}
    executor = None
    top = None if need_amount else ocr_tools.HEADER_FRACTION
    for index in pages:
        page = doc.load_page(index)
        text = page.get_text()
        wants = ocr_tools.page_needs_ocr(text, page, rules["ocr_mode"])
        looked[index] = (text, wants)
        if wants and ocr_tools.match_exact(keys, text) is None and ocr_tools.online_usable(cfg):
            if executor is None:
                executor = ThreadPoolExecutor(ONLINE_PARALLEL)
            image = ocr_tools.render_gray(page, ocr_tools.OCR_DPI_ONLINE, top)
            prefetch[index] = (top, executor.submit(ocr_tools.read_online, image, cfg))
    return executor, looked, prefetch
