"""Filesystem paths for the RDC Payables Report tool.

Mapping data itself now lives in the suite's shared MySQL database (see
models.py) rather than in an xlsx file with a file lock. This module points
at the original seed workbook (backend/seed_data/vendor-site-code-
mapping.xlsx). Missing legacy rows are added at startup without overwriting
active admin edits or reviving archived mappings.
"""

from app.config import SEED_DIR

SEED_MAPPING_PATH = SEED_DIR / "vendor-site-code-mapping.xlsx"
