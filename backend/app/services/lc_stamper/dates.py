"""Invoice-date extraction from page text (label-bounded, OCR tolerant).

Pure python (re/datetime only) so worker processes import it instantly."""
import re
from datetime import datetime


# ---------------------------------------------------------------------------
# DATE EXTRACTION HELPERS
# ---------------------------------------------------------------------------
_DATE_FORMATS = [
    '%d-%m-%Y', '%d-%m-%y',
    '%d-%b-%Y', '%d-%b-%y',
    '%d-%B-%Y', '%d-%B-%y',
    '%d %b %Y', '%d %b %y',
    '%d %B %Y', '%d %B %y',
    '%b %d %Y', '%b %d %y',
    '%B %d %Y', '%B %d %y',
    '%Y-%m-%d',
]

def _parse_date_str(text):
    """Try to parse a date string in various formats → date object or None."""
    if not text:
        return None
    # Normalize human-friendly forms such as "13th June, 2026" and mixed
    # separators such as "13/06-26" without fuzzy parsing. Fuzzy parsing can
    # mistake invoice or document identifiers for dates.
    cleaned = re.sub(
        r'(?<=\d)(?:st|nd|rd|th)\b', '', str(text).strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'\s*,\s*', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    normalized_separators = re.sub(r'\s*[-/\.]\s*', '-', cleaned)

    for candidate in dict.fromkeys((cleaned, normalized_separators)):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None

# Explicit matching keeps dates bounded, so an ISO date such as 2026-07-01 can
# never be partially matched as 26-07-01 (which strptime interprets as
# 26 July 2001).
_MONTH_NAME_RE = (
    r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|'
    r'Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|'
    r'Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
)
_DATE_SEPARATOR_RE = r'(?:\s*[-/\.]\s*|\s+)'
_DATE_RE = (
    # ISO / year-first numeric date.
    r'(?<!\d)\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}(?!\d)'
    r'|'
    # Day-first numeric date; the two separators may differ (13/06-26).
    r'(?<!\d)\d{1,2}[-/\.]\d{1,2}[-/\.](?:\d{4}|\d{2})(?!\d)'
    r'|'
    # Day-first named month (13th June 2026, 13-Jun-26).
    r'(?<!\d)\d{1,2}(?:st|nd|rd|th)?' + _DATE_SEPARATOR_RE +
    _MONTH_NAME_RE + r'\.?' +
    r'(?:\s*,\s*|' + _DATE_SEPARATOR_RE + r')' +
    r'(?:\d{4}|\d{2})(?!\d)'
    r'|'
    # Month-first named month (Jun 13, 2026, June-13-26).
    r'(?<![A-Za-z0-9])' + _MONTH_NAME_RE + r'\.?' +
    _DATE_SEPARATOR_RE + r'\d{1,2}(?:st|nd|rd|th)?' +
    r'(?:\s*,\s*|' + _DATE_SEPARATOR_RE + r')' +
    r'(?:\d{4}|\d{2})(?!\d)'
)

_INVOICE_DATE_LABEL_RE = re.compile(
    r'\b(?:Tax\s+)?Invoice\s*(?:Date|Dt\.?)\b|'
    r'\bDate\s+of\s+Invoice\b|'
    r'\bInv\.?\s*Date\b',
    re.IGNORECASE,
)

_NEXT_DATE_FIELD_RE = re.compile(
    r'\b(?:Ackno\.?|Acknowledg(?:e)?ment|LC|Posting|DC|Document|Due|EWB)\s*Date\s*:',
    re.IGNORECASE,
)
_NEXT_LINE_FIELD_RE = re.compile(
    r'\n\s*[A-Za-z][A-Za-z0-9 ._()/&\-]{1,48}\s*:',
    re.IGNORECASE,
)


def _first_date_in_text(text):
    """Return the first complete, valid date token in text."""
    for match in re.finditer(_DATE_RE, text, re.IGNORECASE):
        parsed = _parse_date_str(match.group())
        if parsed:
            return parsed
    return None

def _extract_invoice_date_from_page(text):
    """
    Extract the invoice date from page text.
    Tries near the 'Invoice Date' label first; falls back to the first valid date anywhere.
    """
    if not text:
        return None

    # Search a bounded region after every invoice-date label. This tolerates
    # OCR line breaks and two-column PDF extraction while prioritizing the field
    # that is actually being verified.
    labels = list(_INVOICE_DATE_LABEL_RE.finditer(text))
    for label in labels:
        field_text = text[label.end():label.end() + 160].lstrip(' \t:-')
        boundaries = [
            match.start()
            for pattern in (_NEXT_DATE_FIELD_RE, _NEXT_LINE_FIELD_RE)
            if (match := pattern.search(field_text))
        ]
        if boundaries:
            field_text = field_text[:min(boundaries)]
        parsed = _first_date_in_text(field_text)
        if parsed:
            return parsed

    # A visible invoice-date label with an unreadable value must not fall back
    # to another business date such as Ackno.Date, LC Date, or Posting Date.
    if labels:
        return None

    # If OCR lost the label, retain the previous fallback but accept only full,
    # bounded date tokens.
    return _first_date_in_text(text)
