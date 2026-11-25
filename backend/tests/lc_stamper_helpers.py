"""Synthetic invoice PDFs for the LC Stamper tests."""

import datetime as dt
from pathlib import Path

import pymupdf as fitz


def invoice_text(number, date="15-01-2026", total="11,800.00", extra=""):
    return (f"TAX INVOICE\nRDC CONCRETE (INDIA) LIMITED\nInvoice No: {number}\nInvoice Date: {date}\n"
            f"Ackno. Date: 01-01-2020\nItem  Qty  Rate  Amount\nCement  10  1,000.00  10,000.00\n"
            f"IGST 18%  1,800.00\nGrand Total  {total}\n{extra}")


def make_pdf(path, pages, rotate=None, size=(595, 842)):
    """``pages``: a list of page texts (a str) - or None for a blank page."""
    doc = fitz.open()
    for i, text in enumerate(pages):
        page = doc.new_page(width=size[0], height=size[1])
        if text:
            page.insert_textbox(fitz.Rect(40, 40, size[0] - 40, size[1] - 40), text, fontsize=11)
        if rotate and rotate.get(i):
            page.set_rotation(rotate[i])
    doc.save(str(path))
    doc.close()
    return Path(path)
