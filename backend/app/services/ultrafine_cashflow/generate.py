"""Build and store the latest cashflow workbook of a month."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app import config

from .models import CashflowFile
from .resolver import load_maps, unmapped_summary
from .summary import month_figures
from .workbook import write_cashflow_workbook

STORAGE_DIR = config.DATA_DIR / "ultrafine_cashflow"


def file_name_for(month: str) -> str:
    return datetime.strptime(month, "%Y-%m").strftime("UF_Cashflow_%b_%y.xlsx")


def generate_month(db: Session, month: str, user_id: int | None, allow_unmapped: bool = False) -> dict:
    """Write the workbook, replacing the month's stored copy. Does not write
    anything when a transaction is still unmapped - the caller gets what has
    to be fixed instead - unless `allow_unmapped` says to write it anyway, in
    which case the unmapped lines are flagged in the workbook. Caller commits."""
    maps = load_maps(db)
    figures = month_figures(db, month, maps)
    if not figures.allocations:
        return {"month": month, "status": "no_data"}

    issues = [row for row in figures.allocations if row.issues]
    if issues and not allow_unmapped:
        return {
            "month": month, "status": "needs_mapping_fix",
            "issue_count": len(issues), "unmapped": unmapped_summary(figures.allocations, maps),
        }

    target_dir = STORAGE_DIR / month
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / file_name_for(month)
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", dir=str(target_dir))
    os.close(fd)
    try:
        stats = write_cashflow_workbook(Path(tmp_name), figures, maps)
        os.replace(tmp_name, final_path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)

    row = db.query(CashflowFile).filter(CashflowFile.month == month).first()
    if row is None:
        row = CashflowFile(month=month, file_name=final_path.name, file_path=str(final_path), version=0)
        db.add(row)
    row.version = (row.version or 0) + 1
    row.file_name, row.file_path = final_path.name, str(final_path)
    row.txn_count = len({a.txn_id for a in figures.allocations})
    row.source = "generated"
    row.generated_by, row.generated_at, row.is_deleted = user_id, datetime.utcnow(), False
    db.flush()
    return {
        "month": month, "status": "generated", "version": row.version, "file_name": row.file_name,
        "earlier_issue_count": figures.earlier_issue_count, "unmapped_count": len(issues), **stats,
    }


def attach_workbook(db: Session, month: str, source: Path, user_id: int | None) -> dict:
    """Store an existing cashflow workbook as the month's latest file (e.g.
    the hand-made October one). A later generate replaces it like any other.
    Caller commits."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(source, read_only=True)
        wb.close()
    except Exception as exc:  # noqa: BLE001 - any unreadable file is the user's to fix
        raise ValueError("That file is not a valid .xlsx workbook.") from exc

    target_dir = STORAGE_DIR / month
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / file_name_for(month)
    shutil.copyfile(source, final_path)

    row = db.query(CashflowFile).filter(CashflowFile.month == month).first()
    if row is None:
        row = CashflowFile(month=month, file_name=final_path.name, file_path=str(final_path), version=0)
        db.add(row)
    row.version = (row.version or 0) + 1
    row.file_name, row.file_path, row.source = final_path.name, str(final_path), "uploaded"
    row.txn_count = 0
    row.generated_by, row.generated_at, row.is_deleted = user_id, datetime.utcnow(), False
    db.flush()
    return {"month": month, "version": row.version, "file_name": row.file_name}
