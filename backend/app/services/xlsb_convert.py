"""Convert a finished .xlsx report to .xlsb (Excel Binary Workbook).

.xlsb holds exactly the same sheets, values, formulas and formats in a much
smaller file, which is what lets a report that is still over the email limit
go out as one attachment. It is written by app.services.xlsb_writer in pure
Python: no Excel, no COM and no other software is involved. The work runs on
the CPU process pool, so it never holds up the web server.

Anything the writer does not convert (a feature of a workbook this application
does not produce) or any failure just returns False with no half-written file
left behind; the caller then falls back to splitting the report into several
mails, which loses nothing either.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.jobs import JobCancelled, run_cpu_phase
from app.services import xlsb_writer

logger = logging.getLogger(__name__)


def convert_to_xlsb(source: str | Path, destination: str | Path) -> bool:
    """Write ``source`` (.xlsx) as ``destination`` (.xlsb). True only if a
    non-empty file was produced."""
    destination = Path(destination)
    try:
        run_cpu_phase(xlsb_writer.convert, str(source), str(destination))
    except JobCancelled:
        raise
    except xlsb_writer.Unsupported as exc:
        logger.warning("xlsb conversion skipped: %s", exc)
    except Exception:
        logger.exception("xlsb conversion failed")
    else:
        if destination.exists() and destination.stat().st_size > 0:
            return True
    destination.unlink(missing_ok=True)
    return False
