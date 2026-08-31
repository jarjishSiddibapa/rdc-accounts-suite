"""Shared exception type for the ERP converter pipeline.

Lives in its own module (rather than converter.py) so xlsx_writer.py can
raise it too without an import cycle (converter.py already imports
xlsx_writer.py).
"""


class ConversionError(Exception):
    """A recognized, user-actionable conversion failure - the router
    surfaces its message verbatim instead of a generic internal-error
    string."""
