"""Month figures for the Summary sheet.

All amounts on the Summary sheet are in Lakhs. Opening balances chain
forward: a month's opening is the balance typed for it (if any), otherwise
the previous month's closing, so after the first month nobody retypes them.
"""

from __future__ import annotations

import ast
import operator
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import (
    CfLocation,
    MonthInput,
    PlanItem,
    PlanSection,
    SummaryGroup,
    Transaction,
)
from .resolver import Allocation, Maps, allocations_for_month, load_maps

LAKH = Decimal("100000")


@dataclass
class SummaryRow:
    cf_id: int
    name: str
    authorized_person: str
    group: str | None
    payable_pct: float
    reserve_pct: float | None
    opening: float = 0.0
    collection: float = 0.0
    payable: float = 0.0
    reserve: float | None = None
    paid: float = 0.0
    statutory: float = 0.0
    closing: float = 0.0
    opening_is_manual: bool = False


@dataclass
class MonthFigures:
    month: str
    rows: list[SummaryRow] = field(default_factory=list)
    allocations: list[Allocation] = field(default_factory=list)
    earlier_issue_count: int = 0


# ── safe arithmetic for planning amounts ────────────────────────────────────

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def evaluate_amount(expression: str | None) -> float | None:
    """Evaluate "20+3+1.5" style amounts. Anything else yields None."""
    text = (expression or "").strip().lstrip("=").replace(",", "")
    if not text:
        return None

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.operand))
        raise ValueError("unsupported expression")

    try:
        return walk(ast.parse(text, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, RecursionError):
        return None


def is_plain_number(expression: str | None) -> bool:
    try:
        float((expression or "").replace(",", ""))
        return True
    except ValueError:
        return False


# ── figures ─────────────────────────────────────────────────────────────────

def months_with_data(db: Session) -> list[str]:
    seen = {
        dt.strftime("%Y-%m")
        for (dt,) in db.query(Transaction.txn_dt).filter(Transaction.is_deleted == False).all()  # noqa: E712
    }
    return sorted(seen)


def summary_locations(db: Session) -> list[tuple[CfLocation, str | None]]:
    groups = {g.id: g.name for g in db.query(SummaryGroup).filter(SummaryGroup.is_deleted == False)}  # noqa: E712
    rows = (
        db.query(CfLocation)
        .filter(CfLocation.is_deleted == False, CfLocation.show_in_summary == True)  # noqa: E712
        .order_by(CfLocation.sort_order, CfLocation.id)
        .all()
    )
    return [(row, groups.get(row.group_id)) for row in rows]


def _totals(allocations: list[Allocation]) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    collected: dict[str, Decimal] = defaultdict(Decimal)
    paid: dict[str, Decimal] = defaultdict(Decimal)
    for row in allocations:
        if not row.cf_location:
            continue
        (collected if row.direction == "C" else paid)[row.cf_location] += row.amount
    return collected, paid


def month_figures(db: Session, month: str, maps: Maps | None = None) -> MonthFigures:
    maps = maps or load_maps(db)
    locations = summary_locations(db)
    inputs = {
        (m.month, m.cf_location_id): m
        for m in db.query(MonthInput).filter(MonthInput.is_deleted == False)  # noqa: E712
    }
    chain = [m for m in months_with_data(db) if m < month] + [month]

    previous_closing: dict[int, float] = {}
    figures = MonthFigures(month=month)
    for current in chain:
        allocations = allocations_for_month(db, current, maps)
        collected, paid = _totals(allocations)
        rows: list[SummaryRow] = []
        for cf, group in locations:
            manual = inputs.get((current, cf.id))
            opening_value = manual.opening_balance if manual and manual.opening_balance is not None else None
            statutory = float(manual.statutory_amount) if manual and manual.statutory_amount is not None else 0.0
            row = SummaryRow(
                cf_id=cf.id, name=cf.name, authorized_person=cf.authorized_person or "", group=group,
                payable_pct=float(cf.payable_pct),
                reserve_pct=float(cf.reserve_pct) if cf.reserve_pct is not None else None,
                opening=float(opening_value) if opening_value is not None else previous_closing.get(cf.id, 0.0),
                opening_is_manual=opening_value is not None,
                collection=float(collected.get(cf.name, Decimal(0)) / LAKH),
                paid=float(paid.get(cf.name, Decimal(0)) / LAKH),
                statutory=statutory,
            )
            row.payable = row.collection * row.payable_pct / 100
            row.reserve = row.collection * row.reserve_pct / 100 if row.reserve_pct is not None else None
            row.closing = row.opening + row.payable - row.paid - row.statutory
            rows.append(row)
        previous_closing = {row.cf_id: row.closing for row in rows}
        if current == month:
            figures.rows = rows
            figures.allocations = allocations
        else:
            figures.earlier_issue_count += sum(1 for a in allocations if a.issues)
    return figures


def plan_blocks(db: Session, month: str) -> list[dict]:
    """Planning sections of `month`, grouped by block number."""
    sections = (
        db.query(PlanSection)
        .filter(PlanSection.month == month, PlanSection.is_deleted == False)  # noqa: E712
        .order_by(PlanSection.block_no, PlanSection.sort_order, PlanSection.id)
        .all()
    )
    items: dict[int, list[PlanItem]] = defaultdict(list)
    for item in (
        db.query(PlanItem)
        .filter(PlanItem.section_id.in_([s.id for s in sections] or [0]), PlanItem.is_deleted == False)  # noqa: E712
        .order_by(PlanItem.sort_order, PlanItem.id)
    ):
        items[item.section_id].append(item)

    blocks: dict[int, list[dict]] = defaultdict(list)
    for section in sections:
        blocks[section.block_no].append({
            "id": section.id, "title": section.title,
            "items": [
                {"id": i.id, "label": i.label, "amount_expr": i.amount_expr,
                 "amount": evaluate_amount(i.amount_expr)}
                for i in items[section.id]
            ],
        })
    return [{"block_no": number, "sections": sections_} for number, sections_ in sorted(blocks.items())]
