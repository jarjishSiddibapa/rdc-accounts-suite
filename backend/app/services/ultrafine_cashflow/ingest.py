"""Store bank-statement lines, ignoring lines already stored.

Statements for consecutive months often overlap (a month-end line appears in
two exports, or the same month is exported twice). Every line therefore gets
a deterministic ``dedupe_key`` from what identifies the bank event itself:
account, date and *minute*, debit/credit, amount and description. The
reference number is deliberately left out - CSV exports turn numeric
references into scientific notation (``6.05E+11``) while XLS exports keep
them - and seconds are left out because the CSV export has none.

Two genuinely identical lines in one file (same minute, amount and text) are
told apart by an occurrence counter, so re-uploading the same file adds
nothing, while a file that contains one more identical line than before adds
exactly that one.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy.orm import Session

from .keys import casefold_key
from .models import BankAccount, StatementUpload, Transaction
from .statement_reader import Statement


def _base_key(account_no: str, line) -> str:
    return "|".join(
        (
            account_no,
            line.txn_dt.strftime("%Y-%m-%d %H:%M"),
            line.direction,
            f"{line.amount:.2f}",
            casefold_key(line.description),
        )
    )


def _dedupe_key(base: str, occurrence: int) -> str:
    return hashlib.sha1(f"{base}|{occurrence}".encode("utf-8")).hexdigest()


def month_of(dt) -> str:
    return dt.strftime("%Y-%m")


def get_or_create_account(db: Session, account_no: str) -> BankAccount:
    account = db.query(BankAccount).filter(BankAccount.account_no == account_no).first()
    if account is None:
        account = BankAccount(account_no=account_no, label="")
        db.add(account)
        db.flush()
    elif account.is_deleted:
        account.is_deleted = False
    return account


def ingest_statement(db: Session, statement: Statement, filename: str, user_id: int | None) -> dict:
    """Insert the new lines of `statement`. Caller commits."""
    account = get_or_create_account(db, statement.account_no)
    first = min(line.txn_dt for line in statement.lines)
    last = max(line.txn_dt for line in statement.lines)

    existing = {
        key
        for (key,) in db.query(Transaction.dedupe_key)
        .filter(
            Transaction.account_id == account.id,
            Transaction.txn_dt >= first - timedelta(days=1),
            Transaction.txn_dt <= last + timedelta(days=1),
        )
        .all()
    }

    upload = StatementUpload(
        account_id=account.id,
        uploaded_by=user_id,
        filename=filename[:255],
        period_from=statement.period_from,
        period_to=statement.period_to,
        line_count=len(statement.lines),
    )
    db.add(upload)
    db.flush()

    seen: Counter[str] = Counter()
    per_month: dict[str, dict[str, int]] = defaultdict(lambda: {"new": 0, "duplicate": 0})
    new_count = duplicate_count = 0
    for line in statement.lines:
        base = _base_key(statement.account_no, line)
        seen[base] += 1
        key = _dedupe_key(base, seen[base])
        month = month_of(line.txn_dt)
        if key in existing:
            duplicate_count += 1
            per_month[month]["duplicate"] += 1
            continue
        existing.add(key)
        db.add(
            Transaction(
                account_id=account.id,
                upload_id=upload.id,
                txn_dt=line.txn_dt,
                direction=line.direction,
                amount=line.amount,
                description=line.description,
                reference_no=line.reference_no,
                dedupe_key=key,
            )
        )
        new_count += 1
        per_month[month]["new"] += 1

    upload.new_count = new_count
    upload.duplicate_count = duplicate_count
    db.flush()
    return {
        "upload_id": upload.id,
        "account_no": statement.account_no,
        "line_count": len(statement.lines),
        "new_count": new_count,
        "duplicate_count": duplicate_count,
        "months": {month: counts for month, counts in sorted(per_month.items())},
        "header_warnings": statement.check_against_header(),
    }
