"""Indian regional conventions shared by backend services.

Database timestamps remain UTC.  These helpers are used only at business-time
and presentation boundaries so the application behaves consistently no matter
which timezone the server itself uses.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from numbers import Real


IST = timezone(timedelta(hours=5, minutes=30), name="IST")
UTC = timezone.utc


def now_ist() -> datetime:
    """Return the current timezone-aware time in Indian Standard Time."""
    return datetime.now(IST)


def today_ist() -> date:
    """Return today's calendar date in India."""
    return now_ist().date()


def to_ist(value: datetime) -> datetime:
    """Convert a datetime to IST; legacy naive database values are UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def to_ist_iso(value: datetime | None) -> str | None:
    """Serialize a datetime as an unambiguous ISO-8601 IST timestamp."""
    return to_ist(value).isoformat() if value is not None else None


def format_indian_number(value: Real, decimal_places: int | None = None) -> str:
    """Format a number using Indian lakh/crore digit grouping.

    Examples: 12345678 -> ``1,23,45,678`` and 123456.5 -> ``1,23,456.50``.
    """
    numeric = Decimal(str(value))
    if decimal_places is None:
        decimal_places = 0 if numeric == numeric.to_integral_value() else 2

    sign = "-" if numeric < 0 else ""
    rendered = f"{abs(numeric):.{decimal_places}f}"
    integer, separator, fraction = rendered.partition(".")

    if len(integer) > 3:
        final_three = integer[-3:]
        leading = integer[:-3]
        pairs: list[str] = []
        while len(leading) > 2:
            pairs.insert(0, leading[-2:])
            leading = leading[:-2]
        if leading:
            pairs.insert(0, leading)
        integer = ",".join([*pairs, final_three])

    suffix = f"{separator}{fraction}" if separator else ""
    return f"{sign}{integer}{suffix}"
