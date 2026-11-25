"""RDC LC Stamper Application: find invoice pages in PDFs, check them against a list, stamp them.

Qt-free and import-light: the page scanning runs in worker processes (see ``analysis``), which import this package
without the web framework, the database or pandas.
"""
