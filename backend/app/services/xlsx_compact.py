"""Make a large .xlsx much smaller without changing what Excel shows.

openpyxl writes the address of every cell (``<c r="O109538" ...>``) next to
the row number the cell already sits in. On a report of ~216,000 rows x 23
columns that is five million redundant addresses, and removing them takes a
26 MB workbook down to about 11 MB. The cell address is optional in the
spreadsheet standard (a cell without one simply follows the previous cell in
its row), and the result is still an ordinary ``.xlsx`` that opens in Excel
with no repair, with every value and every format unchanged. Only rows whose
cells are consecutive are touched (see _strip_row).

The sheets are rewritten as a stream, so even a 250 MB sheet never has to sit
in memory.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path

_CELL_ADDRESS = re.compile(rb'<c r="([A-Z]{1,3})([0-9]{1,7})"')
_ROW = re.compile(rb"<row\b[^>]*?(?<!/)>.*?</row>", re.DOTALL)
_CHUNK_BYTES = 8 * 1024 * 1024


def _column_number(letters: bytes) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + (letter - 64)
    return number


def _strip_row(match: re.Match) -> bytes:
    """Drop the cell addresses of one row - but only when its cells run A, B,
    C, ... with no gap. The writer leaves an empty cell out altogether and
    relies on the address to place the next one, so a row with a gap keeps its
    addresses and is copied unchanged."""
    row = match.group(0)
    expected = 1
    for letters, _ in _CELL_ADDRESS.findall(row):
        if _column_number(letters) != expected:
            return row
        expected += 1
    return _CELL_ADDRESS.sub(b"<c", row)


def _strip_stream(source, sink) -> None:
    """Copy ``source`` to ``sink`` row by row. Chunks are cut just after a
    ``</row>``, so a row is never split between two chunks."""
    carry = b""
    while True:
        block = source.read(_CHUNK_BYTES)
        if not block:
            break
        data = carry + block
        end = data.rfind(b"</row>")
        if end == -1:
            carry = data
            continue
        cut = end + len(b"</row>")
        sink.write(_ROW.sub(_strip_row, data[:cut]))
        carry = data[cut:]
    if carry:
        sink.write(_ROW.sub(_strip_row, carry))


def compact_xlsx(source: str | Path, destination: str | Path) -> None:
    """Write a smaller, equivalent copy of ``source`` to ``destination``."""
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(
        destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zout:
        for info in zin.infolist():
            is_sheet = info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml")
            if is_sheet:
                with zin.open(info) as fin, zout.open(info.filename, "w") as fout:
                    _strip_stream(fin, fout)
            else:
                zout.writestr(info.filename, zin.read(info.filename))


def compact_xlsx_in_place(path: str | Path) -> int:
    """Replace ``path`` with its compact copy and return the bytes saved."""
    path = Path(path)
    before = path.stat().st_size
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", dir=str(path.parent))
    os.close(fd)
    try:
        compact_xlsx(path, tmp_name)
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return before - path.stat().st_size
