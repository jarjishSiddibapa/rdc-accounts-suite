"""Plain, picklable data contracts shared by the engine, the workers and the router."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

OCR_MODES = ("off", "auto", "always")      # off: never, auto: only when the page cannot be read, always: every page
MODE_STAMP = "stamp"                       # keep the matched pages and stamp them
MODE_FILTER = "filter"                     # keep the matched pages as they are (no stamp)
OUTPUT_PER_DOCUMENT = "per_document"       # one output PDF for every uploaded PDF
OUTPUT_COMBINED = "combined"               # one output PDF for the whole batch


@dataclass
class StampSpec:
    """What to draw and where. ``rel_x``/``rel_y`` are the top-left corner of the text box on the VISUAL page
    (after /Rotate), as a fraction of the page width/height. Empty text means no stamp."""
    text: str = ""
    font_size: float = 12.0                  # PDF points
    color: tuple = (0.0, 0.0, 0.0)           # RGB 0..1
    rel_x: float = 0.05
    rel_y: float = 0.05
    bold: bool = True


@dataclass
class Rules:
    """How a page is accepted. Invoice number is always matched."""
    match_dates: bool = False                # a different date on the page: leave the page out (else keep it, flagged)
    match_amounts: bool = False              # a different amount on the page: leave the page out (else keep, flagged)
    amount_tolerance: float = 1.0            # rupees the two amounts may differ by
    skip_blank: bool = True
    ocr_mode: str = "auto"
    online_ocr: bool = True                  # try the online OCR service before the offline engine
    keep_duplicates: bool = False            # a second page of an invoice already kept: keep it too (else skip)


@dataclass
class PageResult:
    """What the scan found on one page. ``doc``/``page`` are the position in the uploaded batch."""
    doc: int
    page: int
    key: Optional[str]                       # the invoice number found, "ALL" (no list) or None
    status: str                              # MATCH | NO_MATCH | SKIPPED_BLANK | ERROR: ...
    pdf_date: Optional[date] = None
    pdf_amounts: tuple = ()                  # every amount-like number of the page that could be the invoice total
    pdf_total: Optional[float] = None        # the labelled invoice total, when the page has one
    read_by: str = "text"                    # text | online | offline  (how the page was read)


@dataclass
class Target:
    """One row of the invoice list."""
    invoice: str
    date: Optional[date] = None
    amount: Optional[float] = None
    extra: dict = field(default_factory=dict)    # the other columns of the row (usable in the stamp text)
