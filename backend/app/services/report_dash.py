"""Shared "-" convention for generated workbooks: a zero or an empty cell
shows a dash instead of 0 / 0.00 / blank."""

from __future__ import annotations

import pandas as pd

DASH = "-"
# The third section of an Excel number format is the zero case.
FMT_INT = '#,##0;-#,##0;"-"'
FMT_DEC = '#,##0.00;-#,##0.00;"-"'


def is_blank(val) -> bool:
    """None, NaN / NA / NaT, or an empty or whitespace-only string."""
    if val is None or val is pd.NA or val is pd.NaT:
        return True
    if isinstance(val, float) and val != val:
        return True
    return isinstance(val, str) and not val.strip()
