class StatementParseError(Exception):
    """A bank statement upload didn't parse - always a user-facing message,
    never a raw traceback."""
