"""Read a bank statement (.csv / .xls / .xlsx) into normalized lines.

Statements arrive in slightly different shapes - an HDFC CSV export with
``C.D.Falg``, an HDFC XLS export with ``Debit / Credit`` - so columns are
found by name, not by position. Each file starts with a block of
``label : value`` details (account number, period, opening/closing balance,
debit/credit totals) which are returned so an upload can be checked against
the bank's own totals.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from app.jobs import JobUserError
from app.services import tabular_input

_DATETIME_FORMATS = (
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
    "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
)

_ALIASES = {
    "date": ("transaction date", "txn date", "tran date", "date"),
    "description": ("transaction description", "description", "narration", "particulars"),
    "amount": ("transaction amount", "amount"),
    "flag": ("c.d.falg", "c.d.flag", "debit / credit", "debit/credit", "dr/cr", "cr/dr", "cd flag"),
    "debit": ("debit amount", "debit", "withdrawal", "withdrawals", "withdrawal amt."),
    "credit": ("credit amount", "credit", "deposit", "deposits", "deposit amt."),
    "reference": ("reference no.", "reference no", "chq/ref no", "ref no", "reference"),
    "value_date": ("value date",),
}


@dataclass
class StatementLine:
    txn_dt: datetime
    direction: str  # "D" or "C"
    amount: Decimal
    description: str
    reference_no: str = ""


@dataclass
class Statement:
    account_no: str
    lines: list[StatementLine] = field(default_factory=list)
    period_from: date | None = None
    period_to: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    header_debit_total: Decimal | None = None
    header_credit_total: Decimal | None = None
    header_debit_count: int | None = None
    header_credit_count: int | None = None

    @property
    def debit_total(self) -> Decimal:
        return sum((l.amount for l in self.lines if l.direction == "D"), Decimal("0"))

    @property
    def credit_total(self) -> Decimal:
        return sum((l.amount for l in self.lines if l.direction == "C"), Decimal("0"))

    def check_against_header(self) -> list[str]:
        """Human-readable mismatches between parsed lines and bank totals."""
        problems: list[str] = []
        tolerance = Decimal("1.00")
        for label, header, actual in (
            ("debit", self.header_debit_total, self.debit_total),
            ("credit", self.header_credit_total, self.credit_total),
        ):
            if header is not None and abs(header - actual) > tolerance:
                problems.append(
                    f"Total {label} amount in the statement header is {header:,.2f} "
                    f"but the lines add up to {actual:,.2f}."
                )
        for label, header, direction in (
            ("debit", self.header_debit_count, "D"),
            ("credit", self.header_credit_count, "C"),
        ):
            actual_count = sum(1 for l in self.lines if l.direction == direction)
            if header is not None and header != actual_count:
                problems.append(
                    f"Total {label} count in the statement header is {header} "
                    f"but {actual_count} {label} lines were read."
                )
        return problems


# ── helpers ─────────────────────────────────────────────────────────────────

def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _norm(value) -> str:
    return re.sub(r"\s+", " ", _text(value)).casefold()


def parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raw = _text(value)
    if not raw:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _to_decimal(value) -> Decimal | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and pd.isna(value):
            return None
        return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    raw = _text(value).replace(",", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _to_date(value) -> date | None:
    parsed = parse_datetime(value)
    return parsed.date() if parsed else None


def _read_rows(path: Path) -> list[list]:
    """The statement as rows - any Excel flavour, CSV/TSV or an HTML export;
    the real type is detected from the file's content."""
    grid = tabular_input.read_grid(path)
    return grid.where(grid.notna(), None).values.tolist()


def _find_header_row(rows: list[list]) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(rows):
        names = [_norm(cell) for cell in row]
        columns: dict[str, int] = {}
        for key, aliases in _ALIASES.items():
            for position, name in enumerate(names):
                if name in aliases and key not in columns:
                    columns[key] = position
        has_amount = "amount" in columns or ("debit" in columns and "credit" in columns)
        has_direction = "flag" in columns or ("debit" in columns and "credit" in columns)
        if "date" in columns and "description" in columns and has_amount and has_direction:
            return index, columns
    raise JobUserError(
        "This does not look like a bank statement - no row with Transaction Date, "
        "Description, Amount and a Debit/Credit flag was found."
    )


def _read_details(rows: list[list]) -> dict[str, str]:
    """The ``label : value`` block above the table, for both layouts
    (``Account No : 123`` in one cell, or label and value in two cells)."""
    details: dict[str, str] = {}
    for row in rows:
        first = _text(row[0]) if row else ""
        if not first:
            continue
        if ":" in first:
            label, _, value = first.partition(":")
            details[_norm(label)] = value.strip()
        elif len(row) > 1 and _text(row[1]):
            details[_norm(first)] = _text(row[1])
    return details


def _detail(details: dict[str, str], *names: str) -> str:
    for name in names:
        if name in details:
            return details[name]
    return ""


def read_statement(path: str | Path) -> Statement:
    path = Path(path)
    rows = _read_rows(path)
    header_index, columns = _find_header_row(rows)
    details = _read_details(rows[:header_index])

    account_no = re.sub(r"\D", "", _detail(details, "account no", "account number", "account no."))
    if not account_no:
        raise JobUserError("The statement does not show an account number, so it cannot be filed.")

    statement = Statement(
        account_no=account_no,
        period_from=_to_date(_detail(details, "from date")),
        period_to=_to_date(_detail(details, "to date")),
        opening_balance=_to_decimal(_detail(details, "opening balance")),
        closing_balance=_to_decimal(_detail(details, "closing balance")),
        header_debit_total=_to_decimal(_detail(details, "total debit amount")),
        header_credit_total=_to_decimal(_detail(details, "total credit amount")),
    )
    for attr, label in (("header_debit_count", "total debit count"), ("header_credit_count", "total credit count")):
        number = _to_decimal(_detail(details, label))
        setattr(statement, attr, int(number) if number is not None else None)

    def cell(row: list, key: str):
        position = columns.get(key)
        return row[position] if position is not None and position < len(row) else None

    for row in rows[header_index + 1:]:
        txn_dt = parse_datetime(cell(row, "date"))
        if txn_dt is None:
            continue  # footer / blank lines
        description = _text(cell(row, "description"))
        if "flag" in columns:
            amount = _to_decimal(cell(row, "amount"))
            flag = _text(cell(row, "flag")).upper()[:1]
            direction = "D" if flag == "D" else "C" if flag == "C" else ""
        else:
            debit, credit = _to_decimal(cell(row, "debit")), _to_decimal(cell(row, "credit"))
            if debit:
                amount, direction = debit, "D"
            elif credit:
                amount, direction = credit, "C"
            else:
                amount, direction = None, ""
        if amount is None or direction == "":
            raise JobUserError(
                f"Row dated {txn_dt:%d-%m-%Y %H:%M} ('{description[:40]}') has no readable "
                "amount or debit/credit flag."
            )
        statement.lines.append(
            StatementLine(
                txn_dt=txn_dt,
                direction=direction,
                amount=abs(amount).quantize(Decimal("0.01")),
                description=description,
                reference_no=_text(cell(row, "reference"))[:64],
            )
        )
    if not statement.lines:
        raise JobUserError("The statement contains no transaction lines.")
    if statement.period_from is None:
        statement.period_from = min(l.txn_dt for l in statement.lines).date()
    if statement.period_to is None:
        statement.period_to = max(l.txn_dt for l in statement.lines).date()
    return statement
