"""Which pages are kept, and the report rows - pure rules, no files, no PDF library.

The rules (the same for every document of a batch, applied in the order the documents were uploaded):

* a page is the invoice of the list row whose date and amount (when the list has them) do not contradict the page;
  an exact fit wins;
* the first page of an invoice is kept; a second page of the same list row is "Duplicate - Skipped" (or kept when the
  run says so);
* a date or amount that differs: the page is left out when "match dates" / "match amounts" is on, otherwise it is
  kept and flagged in the report;
* a list row that appears on no page is reported "Not Found in PDF".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import amounts as amount_tools
from .models import PageResult, Rules, Target

ST_FOUND = "✓ Found"
ST_VERIFIED = "✓ Verified"
ST_PARTIAL = "⚠ Partial ({what}?)"
ST_FLAGGED = "⚠ Kept ({what} Mismatch)"
ST_EXCLUDED = "✗ {what} Mismatch"
ST_DUPLICATE = "⚠ Duplicate — Skipped"
ST_DUPLICATE_KEPT = "⚠ Duplicate — Kept"
ST_NOT_FOUND = "✗ Not Found in PDF"
ST_UNREADABLE = "✗ Page Not Readable"

COL_INVOICE = "Invoice No"


@dataclass
class Row:
    """One line of the report."""
    invoice: str
    status: str
    target: Optional[Target] = None
    document: str = ""
    page: Optional[int] = None                 # 1-based page of the uploaded document
    out_page: Optional[int] = None             # 1-based page in the output file (set when the output is built)
    pdf_date: Optional[date] = None
    pdf_amount: Optional[float] = None
    date_match: str = "—"
    amount_match: str = "—"
    read_by: str = ""
    kept: bool = False


@dataclass
class Decision:
    keep: list = field(default_factory=list)       # [(doc_index, page_index, Target|None, Row|None)] in page order
    rows: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def _tick(state: Optional[bool], applicable: bool) -> str:
    if not applicable:
        return "—"
    return "?" if state is None else ("✓" if state else "✗")


def _check(entry: Target, result: PageResult, tolerance: float) -> tuple:
    """(date_ok, amount_ok): True / False / None (cannot tell)."""
    date_ok = None
    if entry.date is not None and result.pdf_date is not None:
        date_ok = entry.date == result.pdf_date
    amount_ok = None
    if entry.amount is not None and result.pdf_amounts:
        amount_ok = amount_tools.amounts_match(entry.amount, result.pdf_amounts, tolerance)
    return date_ok, amount_ok


def _what(date_bad: bool, amount_bad: bool) -> str:
    return "Date & Amount" if date_bad and amount_bad else ("Date" if date_bad else "Amount")


def decide(results, targets: list, rules: Rules, documents: list) -> Decision:
    """``results``: every PageResult of the batch. ``targets``: the list's Target rows (empty: no list).
    ``documents``: the file names, by document index."""
    from .targets import by_invoice

    index = by_invoice(targets)
    ordered = sorted(results, key=lambda r: (r.doc, r.page))
    out = Decision()
    seen: set = set()                         # (invoice, date, amount) of the list rows already kept
    found: set = set()                        # list rows (invoice, date, amount) matched by some page
    unreadable = ocr_pages = duplicates = 0
    online_pages = offline_pages = 0

    for result in ordered:
        if result.read_by == "online":
            online_pages += 1
        elif result.read_by == "offline":
            offline_pages += 1
        name = documents[result.doc] if result.doc < len(documents) else ""
        status = result.status
        if status == "SKIPPED_BLANK":
            continue
        if status.startswith("ERROR"):
            unreadable += 1
            out.rows.append(Row("", ST_UNREADABLE + f" ({status[7:].strip()[:80]})", None, name, result.page + 1))
            continue
        if not result.key:
            continue
        if result.key == "ALL":                                   # no list: every non-blank page
            out.keep.append((result.doc, result.page, None, None))
            continue

        entries = index.get(result.key, [])
        chosen = None                         # (entry, date_ok, amount_ok)
        for entry in entries:
            date_ok, amount_ok = _check(entry, result, rules.amount_tolerance)
            if date_ok is False or amount_ok is False:
                continue
            chosen = (entry, date_ok, amount_ok)
            if date_ok is not False and amount_ok is not False and (date_ok or entry.date is None) \
                    and (amount_ok or entry.amount is None):
                break                                              # an exact fit wins
        if chosen is None and entries:                             # every row contradicts the page: show the first
            entry = entries[0]
            chosen = (entry, *_check(entry, result, rules.amount_tolerance))
        if chosen is None:
            continue
        entry, date_ok, amount_ok = chosen
        key = (entry.invoice, entry.date, entry.amount)
        found.add(key)

        pdf_amount = result.pdf_total
        if pdf_amount is None and entry.amount is not None:
            pdf_amount = amount_tools.closest(entry.amount, result.pdf_amounts)
        row = Row(entry.invoice, "", entry, name, result.page + 1, None, result.pdf_date, pdf_amount,
                  _tick(date_ok, entry.date is not None), _tick(amount_ok, entry.amount is not None), result.read_by)

        date_bad, amount_bad = date_ok is False, amount_ok is False
        excluded = (date_bad and rules.match_dates) or (amount_bad and rules.match_amounts)
        if excluded:
            row.status = ST_EXCLUDED.format(what=_what(date_bad and rules.match_dates, amount_bad and rules.match_amounts))
            out.rows.append(row)
            continue
        if key in seen:
            duplicates += 1
            if rules.keep_duplicates:
                row.status, row.kept = ST_DUPLICATE_KEPT, True
                out.keep.append((result.doc, result.page, entry, row))
            else:
                row.status = ST_DUPLICATE
            out.rows.append(row)
            continue
        seen.add(key)

        if date_bad or amount_bad:
            row.status = ST_FLAGGED.format(what=_what(date_bad, amount_bad))
        else:
            unknown = [n for n, applicable, state in (("Date", entry.date is not None, date_ok),
                                                      ("Amount", entry.amount is not None, amount_ok))
                       if applicable and state is None]
            if unknown:
                row.status = ST_PARTIAL.format(what=" & ".join(unknown))
            elif entry.date is None and entry.amount is None:
                row.status = ST_FOUND
            else:
                row.status = ST_VERIFIED
        row.kept = True
        out.keep.append((result.doc, result.page, entry, row))
        out.rows.append(row)

    # list rows that no page carries
    not_found = 0
    for entry in targets:
        if (entry.invoice, entry.date, entry.amount) not in found:
            out.rows.append(Row(entry.invoice, ST_NOT_FOUND, entry, "", None, None, None, None,
                                "N/A" if entry.date else "—", "N/A" if entry.amount is not None else "—"))
            not_found += 1

    out.counts = {
        "kept": len(out.keep),
        "duplicates": duplicates,
        "mismatches": sum(1 for r in out.rows if r.status.startswith("✗") and "Mismatch" in r.status),
        "flagged": sum(1 for r in out.rows if r.status.startswith("⚠ Kept (")),
        "not_found": not_found,
        "unreadable": unreadable,
        "pages_scanned": len(ordered),
        "pages_by_text": sum(1 for r in ordered if r.read_by == "text" and r.status != "SKIPPED_BLANK"),
        "pages_by_online_ocr": online_pages,
        "pages_by_offline_ocr": offline_pages,
        "list_rows": len(targets),
        "verified": sum(1 for r in out.rows if r.status == ST_VERIFIED),
    }
    return out
