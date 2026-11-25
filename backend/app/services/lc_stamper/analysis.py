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


def _scan_page(page, doc_index, page_index, keys, rules, cfg, need_date, need_amount) -> PageResult:
    from . import ocr as ocr_tools

    text = page.get_text()
    blank = len(text.strip()) < _BLANK_MIN_CHARS
    ocr_allowed = rules["ocr_mode"] != "off"
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
    try:
        keys = tuple(keys) if keys else ()
        for index in pages:
            try:
                out.append(_scan_page(doc.load_page(index), doc_index, index, keys, rules, cfg, need_date, need_amount))
            except Exception as exc:
                out.append(PageResult(doc_index, index, None, f"ERROR: {exc}"))
    finally:
        doc.close()
    return out
