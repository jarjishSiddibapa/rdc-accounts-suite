"""Processing column-name constants ported from the desktop app's config.py.

Only the constants that processing.py / excel_writers.py actually import are
kept here — the tkinter theme color dictionaries (THEMES, LOG_COLORS,
BRAND_GREEN, BRAND_DARK, FONT_UI, FONT_MONO) are UI-only and are not needed
server-side.
"""

# ── Processing constants ──────────────────────────────────────────────────────
COLS_TO_DROP    = ["Voucher Number", "PO Number"]
COL_MOVE_TO_END = "Supplier Site"
DATA_ANCHOR_COL = "Invoice Number"
MRN_ANCHOR_COL  = "ACCOUNTING PERIOD"
MRN_SITE_COL    = "SUPPLIER SITE"

# ── Uninvoiced Expense PO Report ──────────────────────────────────────────────
PO_ANCHOR_COL    = "PO Number"          # column present in header row 0
PO_APPROVED_DATE = "PO Approved Date"
PO_SITE_COL      = "Supplier Site"
PO_ORG_COL       = "Organization Name"
PO_HDR_COL       = "Header Description"
