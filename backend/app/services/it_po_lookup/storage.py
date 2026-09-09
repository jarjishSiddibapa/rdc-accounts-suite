"""Stores a parsed bank statement, accumulating across uploads. Re-uploading
an overlapping date range never creates duplicate transaction rows - the
unique constraint on (account_number, transaction_date, reference_no,
transaction_amount) makes the insert idempotent.

Uses a single bulk `INSERT IGNORE` (MySQL-specific) rather than one flush
per row: catching IntegrityError per row and calling db.rollback() would
roll back the *whole* uncommitted transaction, including every row already
flushed earlier in the same batch - not just the offending row.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models import PoLookupBankTransaction, PoLookupStatementUpload

from .bank_statement_parser import ParsedStatement


@dataclass
class StoreResult:
    upload_id: int
    row_count: int
    inserted_count: int
    duplicate_count: int


def store_statement(db: Session, parsed: ParsedStatement, filename: str, uploaded_by_id: int) -> StoreResult:
    account_number = parsed.account_number or ""
    values = [
        {
            "account_number": account_number,
            "transaction_date": row.transaction_date,
            "transaction_description": row.transaction_description,
            "transaction_amount": row.transaction_amount,
            "debit_credit": row.debit_credit,
            "reference_no": row.reference_no,
            "value_date": row.value_date,
            "transaction_branch": row.transaction_branch,
            "running_balance": row.running_balance,
        }
        for row in parsed.rows
    ]

    inserted = 0
    if values:
        # Insert against the plain Table (not the ORM-mapped class) so this
        # stays a Core executemany with a real DBAPI rowcount - passing a
        # list of dicts to an Insert(MappedClass) instead triggers
        # SQLAlchemy 2.0's ORM bulk-insert path, whose IteratorResult has
        # no .rowcount at all.
        stmt = mysql_insert(PoLookupBankTransaction.__table__).prefix_with("IGNORE")
        result = db.execute(stmt, values)
        inserted = result.rowcount or 0
    duplicates = len(values) - inserted

    upload = PoLookupStatementUpload(
        filename=filename,
        account_number=parsed.account_number,
        from_date=parsed.from_date,
        to_date=parsed.to_date,
        row_count=len(parsed.rows),
        inserted_count=inserted,
        duplicate_count=duplicates,
        uploaded_by_id=uploaded_by_id,
    )
    db.add(upload)
    db.flush()

    if parsed.rows:
        db.query(PoLookupBankTransaction).filter(
            PoLookupBankTransaction.account_number == account_number,
            PoLookupBankTransaction.transaction_date >= min(r.transaction_date for r in parsed.rows),
            PoLookupBankTransaction.transaction_date <= max(r.transaction_date for r in parsed.rows),
            PoLookupBankTransaction.upload_id.is_(None),
        ).update({PoLookupBankTransaction.upload_id: upload.id}, synchronize_session=False)

    db.commit()
    return StoreResult(upload_id=upload.id, row_count=len(parsed.rows), inserted_count=inserted, duplicate_count=duplicates)
