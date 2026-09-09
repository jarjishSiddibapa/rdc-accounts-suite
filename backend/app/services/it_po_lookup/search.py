"""Combines the ERP document-number lookup with a search of every stored
bank-statement transaction, producing one of four outcomes per PO:

- "found": the document number was located inside a stored bank
  transaction (as a distinct token in its description, or as the exact
  Reference No.) - the UTR is that transaction's Reference No.
- "payment_in_process": ERP has a document number for this PO, but no
  stored bank transaction matches it yet (the statement covering that
  payment likely hasn't been uploaded yet).
- "payment_not_processed": ERP has no payment document number for this PO
  at all (covers every one of the query's own no-document fallback
  statuses - not booked, entry passed but not paid, or booked but unpaid).
- "po_not_found": the PO number doesn't exist in po_headers_all at all -
  distinct from "payment_not_processed" since it's likely a typo, not a
  real business state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import PoLookupBankTransaction

from .oracle_lookup import OracleConfig, NO_DOCUMENT_STATUSES, fetch_document_numbers


@dataclass
class SearchResult:
    po_number: str
    outcome: str
    vendor_name: str | None = None
    document_number: str | None = None
    utr_number: str | None = None
    transaction_date: str | None = None
    value_date: str | None = None
    transaction_description: str | None = None
    transaction_amount: float | None = None


def parse_po_list(raw_text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for chunk in re.split(r"[,\n\r\t]+", raw_text or ""):
        po = chunk.strip()
        if po and po not in seen:
            seen.add(po)
            result.append(po)
    return result


def _find_matching_transaction(db: Session, document_number: str) -> PoLookupBankTransaction | None:
    pattern = re.compile(rf"(?<!\d){re.escape(document_number)}(?!\d)")
    candidates = (
        db.query(PoLookupBankTransaction)
        .filter(
            PoLookupBankTransaction.is_deleted.is_(False),
            or_(
                PoLookupBankTransaction.reference_no == document_number,
                PoLookupBankTransaction.transaction_description.like(f"%{document_number}%"),
            ),
        )
        .order_by(PoLookupBankTransaction.transaction_date.desc())
        .all()
    )
    for row in candidates:
        if row.reference_no == document_number or pattern.search(row.transaction_description):
            return row
    return None


def _amount(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def search_po_numbers(db: Session, oracle_cfg: OracleConfig, raw_po_text: str) -> list[SearchResult]:
    po_numbers = parse_po_list(raw_po_text)
    if not po_numbers:
        return []
    statuses = fetch_document_numbers(oracle_cfg, po_numbers)

    results: list[SearchResult] = []
    for po in po_numbers:
        if po not in statuses:
            results.append(SearchResult(po_number=po, outcome="po_not_found"))
            continue
        raw_status, vendor_name = statuses[po]
        if raw_status in NO_DOCUMENT_STATUSES:
            results.append(SearchResult(po_number=po, outcome="payment_not_processed", vendor_name=vendor_name))
            continue

        document_number = raw_status
        match = _find_matching_transaction(db, document_number)
        if match is None:
            results.append(
                SearchResult(
                    po_number=po, outcome="payment_in_process",
                    vendor_name=vendor_name, document_number=document_number,
                )
            )
            continue
        results.append(
            SearchResult(
                po_number=po,
                outcome="found",
                vendor_name=vendor_name,
                document_number=document_number,
                utr_number=match.reference_no,
                transaction_date=match.transaction_date.isoformat(),
                value_date=match.value_date.isoformat() if match.value_date else None,
                transaction_description=match.transaction_description,
                transaction_amount=_amount(match.transaction_amount),
            )
        )
    return results
