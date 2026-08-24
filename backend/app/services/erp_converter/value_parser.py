"""Conservative text -> typed value detection for ERP report cells.

Oracle ERP report exports render every value as plain text. To make the
output a genuinely *usable* spreadsheet (summable, filterable, sortable)
we try to recover the original data type - but only when it is safe to do
so. Anything ambiguous is left as text so we never corrupt a code like
"007" or an account segment like "001.001.2033".
"""
import re
from datetime import datetime, date

_NUMBER_RE = re.compile(
    r"^\(?-?(?:\d{1,3}(?:,\d{3})*|\d{1,3}(?:,\d{2})*,\d{3})(\.\d+)?\)?-?$"
)
_PLAIN_NUMBER_RE = re.compile(r"^\(?-?\d+(\.\d+)?\)?-?$")

# Hand-rolled date matching instead of datetime.strptime(): strptime
# recompiles/evicts its tiny internal regex cache (5 entries) every time a
# *different* format string is tried, and most cells in an ERP report are
# not dates at all - they'd still pay for every failed format attempt.
# Trying our own precompiled, anchored regexes first and only building a
# datetime once we already know the shape matched is an order of
# magnitude faster on multi-million-cell reports.
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DMY_TEXT_TIME_RE = re.compile(
    r"^(\d{1,2})-([A-Za-z]{3,9})-(\d{2,4}) (\d{1,2}):(\d{2}):(\d{2})$")
_DMY_TEXT_RE = re.compile(r"^(\d{1,2})-([A-Za-z]{3,9})-(\d{2,4})$")
_YMD_SLASH_TIME_RE = re.compile(
    r"^(\d{4})/(\d{1,2})/(\d{1,2}) (\d{1,2}):(\d{2}):(\d{2})$")
_YMD_SLASH_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_DMY_SLASH_TIME_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4}) (\d{1,2}):(\d{2}):(\d{2})$")
_DMY_SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_MONTH_NAME_RE = re.compile(r"^([A-Za-z]{3,9}) (\d{1,2}), *(\d{4})$")
_DMY_DASH_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")


def _pivot_year(yy):
    yy = int(yy)
    if yy < 100:
        return 2000 + yy if yy < 69 else 1900 + yy
    return yy


def _try_parse_date(text):
    """Returns (value, number_format) or None. value is date or datetime."""
    m = _DMY_TEXT_TIME_RE.match(text)
    if m:
        d, mon, y, hh, mm, ss = m.groups()
        month = _MONTHS.get(mon[:3].lower())
        if month:
            year = _pivot_year(y)
            try:
                dt = datetime(year, month, int(d), int(hh), int(mm), int(ss))
            except ValueError:
                return None
            fmt = "dd-mmm-yyyy hh:mm:ss" if len(y) == 4 else "dd-mmm-yy hh:mm:ss"
            return dt, fmt

    m = _DMY_TEXT_RE.match(text)
    if m:
        d, mon, y = m.groups()
        month = _MONTHS.get(mon[:3].lower())
        if month:
            year = _pivot_year(y)
            try:
                dt = date(year, month, int(d))
            except ValueError:
                return None
            fmt = "dd-mmm-yyyy" if len(y) == 4 else "dd-mmm-yy"
            return dt, fmt

    m = _YMD_SLASH_TIME_RE.match(text)
    if m:
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, hh, mm, ss), "yyyy/mm/dd hh:mm:ss"
        except ValueError:
            return None

    m = _YMD_SLASH_RE.match(text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d), "yyyy/mm/dd"
        except ValueError:
            return None

    m = _DMY_SLASH_TIME_RE.match(text)
    if m:
        d, mo, y, hh, mm, ss = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, hh, mm, ss), "dd/mm/yyyy hh:mm:ss"
        except ValueError:
            return None

    m = _DMY_SLASH_RE.match(text)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y, mo, d), "dd/mm/yyyy"
        except ValueError:
            return None

    m = _MONTH_NAME_RE.match(text)
    if m:
        mon, d, y = m.groups()
        month = _MONTHS.get(mon[:3].lower())
        if month:
            try:
                dt = date(int(y), month, int(d))
            except ValueError:
                return None
            fmt = "mmm dd, yyyy" if len(mon) <= 3 else "mmmm dd, yyyy"
            return dt, fmt

    m = _DMY_DASH_RE.match(text)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y, mo, d), "dd-mm-yyyy"
        except ValueError:
            return None

    return None


def _is_safe_numeric(digits_only):
    """Reject leading-zero integers (e.g. '007', '0123') since those are
    almost always codes, not quantities. '0', '0.5', '0.00' stay numeric.
    """
    if not digits_only:
        return True
    if len(digits_only) > 1 and digits_only[0] == "0":
        return False
    return True


def parse_value(text):
    """Return (value, number_format) for a cell's plain text content.

    value is a python str/float/int/datetime/date/None.
    number_format is an Excel format string, or None to use General/Text.
    Falls back to (original_text, None) when nothing safe is detected.
    """
    if text is None:
        return None, None
    stripped = text.strip()
    if stripped == "":
        return None, None

    # ---- numeric ----
    candidate = stripped
    negative = False
    if candidate.startswith("(") and candidate.endswith(")"):
        negative = True
        candidate = candidate[1:-1]
    if candidate.endswith("-") and not candidate.startswith("-"):
        negative = True
        candidate = candidate[:-1]
    if candidate.startswith("-"):
        negative = True
        candidate = candidate[1:]

    is_number = bool(candidate) and (_NUMBER_RE.match(candidate) or _PLAIN_NUMBER_RE.match(candidate))
    if is_number:
        digits_only = candidate.replace(",", "").split(".")[0]
        if _is_safe_numeric(digits_only):
            plain = candidate.replace(",", "")
            try:
                num = float(plain)
            except ValueError:
                num = None
            if num is not None:
                if negative:
                    num = -num
                had_comma = "," in candidate
                decimals = 0
                if "." in candidate:
                    decimals = len(candidate.split(".", 1)[1])
                if decimals:
                    fmt = ("##,##,##0." + "0" * decimals) if had_comma else ("0." + "0" * decimals)
                else:
                    fmt = "##,##,##0" if had_comma else "0"
                if num == int(num) and abs(num) < 1e15:
                    num = int(num)
                return num, fmt

    # ---- date / datetime ----
    date_result = _try_parse_date(stripped)
    if date_result is not None:
        return date_result

    # ---- plain text ----
    return stripped, None
