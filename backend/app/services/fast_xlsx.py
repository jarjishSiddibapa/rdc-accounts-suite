"""A much faster writer for the data rows of a big sheet in an xlsxwriter workbook.

xlsxwriter spends about 8 microseconds on every cell (about 130,000 cells a second): at
five million cells that is most of the time a big report takes to write. The rows here are
produced instead by plain string formatting, which is some twenty times faster, and are
spliced into the workbook xlsxwriter writes for everything else (the header row, column
widths, freeze panes, filter, styles, the workbook parts).

The sheet XML is what xlsxwriter itself writes for the same cells, character for
character: the same cell and row elements, the same escaping, the same number
formatting, and the format numbers are assigned in the same order (a format gets its
number when its first cell is written, as xlsxwriter does). The tests compare the two on
real-shaped data, and ``enabled()`` re-checks it once per process on a small sample and
falls back to xlsxwriter's own writing if the installed xlsxwriter ever behaves differently.

Use ``new_sheet(ws)`` for each big sheet of a ``constant_memory`` workbook, write the header
and the column settings through ``ws`` as usual, write the data rows through the sheet
object (``begin_row`` / ``text`` / ``number`` / ``date`` / ``boolean`` / ``blank`` /
``auto`` / ``end_row``), then close the workbook with ``close_workbook(workbook, sheets)``
instead of ``workbook.close()``.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
import os
import re
import tempfile
import zipfile
from decimal import Decimal
from fractions import Fraction

from app.services.erp_converter.io_retry import with_retry

logger = logging.getLogger(__name__)

_MAX_ROWS = 1_048_576
_MAX_COLS = 16_384
_STRING_MAX = 32_767
_FLUSH_ROWS = 2_000
_CONTROL = re.compile("[\x00-\x08\x0b-\x1f￾￿]")
_PLACEHOLDER = b'<row r="2"><c r="A2" t="inlineStr"><is><t></t></is></c></row>'
_DATETIME_TYPES = (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)

_enabled: bool | None = None


def _column_names(count: int) -> list[str]:
    names = []
    for index in range(count):
        number, name = index + 1, ""
        while number:
            number, remainder = divmod(number - 1, 26)
            name = chr(65 + remainder) + name
        names.append(name)
    return names


class ClassicSheet:
    """The same interface over xlsxwriter's own writing: the fallback, and the reference
    the fast sheet is checked against."""

    def __init__(self, ws):
        self.ws = ws
        self._r = 0

    def begin_row(self, row: int) -> None:
        self._r = row

    def text(self, ci, value, fmt=None):
        self.ws.write_string(self._r, ci, value, fmt)

    def number(self, ci, value, fmt=None):
        self.ws.write_number(self._r, ci, value, fmt)

    def date(self, ci, value, fmt=None):
        self.ws.write_datetime(self._r, ci, value, fmt)

    def boolean(self, ci, value, fmt=None):
        self.ws.write_boolean(self._r, ci, value, fmt)

    def blank(self, ci, fmt=None):
        self.ws.write_blank(self._r, ci, None, fmt)

    def auto(self, ci, value, fmt=None):
        self.ws.write(self._r, ci, value, fmt)

    def end_row(self) -> None:
        pass


class FastSheet:
    """The data rows of one sheet, written as XML text straight to a temporary file."""

    def __init__(self, ws):
        self.ws = ws
        self._cols = _column_names(256)
        self._style: dict[int, str] = {}        # id(format) -> ' s="n"', numbered on first use
        self._serials: dict = {}
        self._parts: list[str] = []
        self._buffer: list[str] = []
        self._buffered_rows = 0
        self._r = 0
        self._row_open = False
        self._started = False
        self._file = None
        self._path = None
        self._dims = None                       # the sheet's dimensions before any data row
        self.rows = 0
        self._min_col = self._max_col = None
        self._max_row = None
        self._min_row = None

    # ---- set-up ---------------------------------------------------------------------------
    def _start(self) -> None:
        ws = self.ws
        self._dims = (ws.dim_rowmin, ws.dim_rowmax, ws.dim_colmin, ws.dim_colmax)
        # a placeholder second row: writing it makes xlsxwriter number the header's formats now,
        # before any data cell, exactly as it does when the first data cell is written
        ws.write_string(1, 0, "")
        ws.dim_rowmin, ws.dim_rowmax, ws.dim_colmin, ws.dim_colmax = self._dims
        fd, self._path = tempfile.mkstemp(suffix=".rows", dir=ws.tmpdir)
        os.close(fd)
        # text mode, as xlsxwriter's own temporary files, so a newline in a value is written alike
        self._file = open(self._path, mode="w", encoding="utf-8")
        self._default_date_format = getattr(ws, "default_date_format", None)
        self._started = True

    def _col(self, ci: int) -> str:
        try:
            return self._cols[ci]
        except IndexError:
            self._cols = _column_names(max(ci + 1, len(self._cols) * 2))
            return self._cols[ci]

    def _s(self, fmt) -> str:
        if fmt is None:
            return ""
        key = id(fmt)
        attr = self._style.get(key)
        if attr is None:
            attr = f' s="{fmt._get_xf_index()}"'
            self._style[key] = attr
        return attr

    # ---- rows -----------------------------------------------------------------------------
    def begin_row(self, row: int) -> None:
        if not self._started:
            self._start()
        self._r = row
        self._number = row + 1
        self._parts = []
        self._skip_row = row >= _MAX_ROWS

    def _add(self, ci: int, xml: str) -> None:
        if ci >= _MAX_COLS or self._skip_row:
            return
        self._parts.append(xml)
        if self._min_col is None:
            self._min_col = self._max_col = ci
        elif ci < self._min_col:
            self._min_col = ci
        elif ci > self._max_col:
            self._max_col = ci

    def end_row(self) -> None:
        if not self._parts:                      # xlsxwriter writes nothing for a row without cells
            return
        row = self._r
        self._max_row = row if self._max_row is None or row > self._max_row else self._max_row
        self._min_row = row if self._min_row is None or row < self._min_row else self._min_row
        self._buffer.append(f'<row r="{self._number}">{"".join(self._parts)}</row>')
        self.rows += 1
        self._buffered_rows += 1
        if self._buffered_rows >= _FLUSH_ROWS:
            self._flush()

    def _flush(self) -> None:
        if self._buffer:
            self._file.write("".join(self._buffer))
            self._buffer = []
            self._buffered_rows = 0

    # ---- cells ----------------------------------------------------------------------------
    def text(self, ci, value, fmt=None):
        if len(value) > _STRING_MAX:
            value = value[:_STRING_MAX]
        if "_x" in value or _CONTROL.search(value):
            value = _escape_control(value)
        if value.startswith("<r>") and value.endswith("</r>"):         # xlsxwriter treats this as a rich string
            self._add(ci, f'<c r="{self._col(ci)}{self._number}"{self._s(fmt)} t="inlineStr"><is>{value}</is></c>')
            return
        preserve = ""
        if value and (value[0].isspace() or value[-1].isspace()) and _preserve_whitespace(value):
            preserve = ' xml:space="preserve"'
        if "&" in value or "<" in value or ">" in value:
            value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self._add(ci, f'<c r="{self._col(ci)}{self._number}"{self._s(fmt)} t="inlineStr"><is><t{preserve}>{value}</t></is></c>')

    def number(self, ci, value, fmt=None):
        if value != value or value in (math.inf, -math.inf):
            # xlsxwriter writes these as error formulas
            if value != value:
                formula, shown = "#NUM!", "#NUM!"
            elif value > 0:
                formula, shown = "1/0", "#DIV/0!"
            else:
                formula, shown = "-1/0", "#DIV/0!"
            self._add(ci, f'<c r="{self._col(ci)}{self._number}"{self._s(fmt)} t="e"><f>{formula}</f><v>{shown}</v></c>')
            return
        if value.__class__ is Fraction:
            value = float(value)
        self._add(ci, f'<c r="{self._col(ci)}{self._number}"{self._s(fmt)}><v>{value:.16G}</v></c>')

    def date(self, ci, value, fmt=None):
        serial = self._serials.get(value)
        if serial is None:
            serial = self.ws._convert_date_time(value)
            self._serials[value] = serial
        if fmt is None:
            fmt = self._default_date_format
        self._add(ci, f'<c r="{self._col(ci)}{self._number}"{self._s(fmt)}><v>{serial:.16G}</v></c>')

    def boolean(self, ci, value, fmt=None):
        self._add(ci, f'<c r="{self._col(ci)}{self._number}"{self._s(fmt)} t="b"><v>{1 if value else 0}</v></c>')

    def blank(self, ci, fmt=None):
        if fmt is not None:                      # an unformatted blank is not written at all
            self._add(ci, f'<c r="{self._col(ci)}{self._number}"{self._s(fmt)}/>')

    def auto(self, ci, value, fmt=None):
        """What ``Worksheet.write`` does with this value (with the workbook's strings_to_* options off)."""
        kind = value.__class__
        if value is None:
            self.blank(ci, fmt)
        elif kind is str:
            if value:
                self.text(ci, value, fmt)
            else:
                self.blank(ci, fmt)
        elif kind is bool:
            self.boolean(ci, value, fmt)
        elif kind in (float, int, Decimal, Fraction):
            self.number(ci, value, fmt)
        elif kind in _DATETIME_TYPES:
            self.date(ci, value, fmt)
        elif isinstance(value, (float, int, Decimal, Fraction)):
            self.number(ci, value, fmt)
        elif isinstance(value, str):
            self.auto(ci, str(value), fmt) if value else self.blank(ci, fmt)
        elif isinstance(value, bool):
            self.boolean(ci, value, fmt)
        elif isinstance(value, _DATETIME_TYPES):
            self.date(ci, value, fmt)
        else:
            try:
                self.number(ci, float(value), fmt)
            except ValueError:
                self.text(ci, str(value), fmt)

    # ---- finish ---------------------------------------------------------------------------
    def _merge_dimensions(self) -> None:
        """Make the sheet's dimensions cover the rows written here, as if xlsxwriter had written them."""
        if self._max_row is None:
            return
        ws = self.ws
        row_min, row_max, col_min, col_max = self._dims
        ws.dim_rowmin = self._min_row if row_min is None else min(row_min, self._min_row)
        ws.dim_rowmax = self._max_row if row_max is None else max(row_max, self._max_row)
        ws.dim_colmin = self._min_col if col_min is None else min(col_min, self._min_col)
        ws.dim_colmax = self._max_col if col_max is None else max(col_max, self._max_col)

    def _close_file(self) -> None:
        if self._file is not None:
            self._flush()
            self._file.close()
            self._file = None

    def discard(self) -> None:
        self._close_file()
        if self._path:
            try:
                os.unlink(self._path)
            except OSError:
                pass
            self._path = None


def _escape_control(value: str) -> str:
    from xlsxwriter.xmlwriter import XMLwriter
    return XMLwriter._escape_control_characters(value)


def _preserve_whitespace(value: str):
    from xlsxwriter.utility import _preserve_whitespace as preserve
    return preserve(value)


def close_workbook(workbook, sheets) -> None:
    """Close ``workbook`` and put the rows of each FastSheet into its sheet. Sheets that are
    ClassicSheet need nothing: they were written through xlsxwriter."""
    fast = [sheet for sheet in sheets if isinstance(sheet, FastSheet)]
    try:
        for sheet in fast:
            if not sheet._started:
                sheet._start()
            sheet._merge_dimensions()
            sheet._close_file()
        workbook.close()
        if fast:
            _splice(workbook.filename, fast)
    finally:
        for sheet in fast:
            sheet.discard()


def _splice(path: str, sheets: list) -> None:
    by_part = {f"xl/worksheets/sheet{sheet.ws.index + 1}.xml": sheet for sheet in sheets}
    tmp = f"{path}.fast"
    try:
        with zipfile.ZipFile(path) as zin, zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zout:
            for info in zin.infolist():
                sheet = by_part.get(info.filename)
                if sheet is None:
                    zout.writestr(info, zin.read(info.filename))
                    continue
                xml = zin.read(info.filename)
                at = xml.find(_PLACEHOLDER)
                if at < 0 or xml.find(_PLACEHOLDER, at + 1) >= 0:
                    raise RuntimeError(f"fast_xlsx: the placeholder row is not in {info.filename}")
                out_info = zipfile.ZipInfo(info.filename, info.date_time)
                out_info.compress_type = zipfile.ZIP_DEFLATED
                out_info.external_attr = info.external_attr
                with zout.open(out_info, "w", force_zip64=True) as out:
                    out.write(xml[:at])
                    with open(sheet._path, "rb") as rows:
                        while True:
                            block = rows.read(4 * 1024 * 1024)
                            if not block:
                                break
                            out.write(block)
                    out.write(xml[at + len(_PLACEHOLDER):])
        with_retry(lambda: os.replace(tmp, path), path, "replace")   # antivirus may hold a fresh file for a moment
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---- choosing the fast path ---------------------------------------------------------------

def _sample(path: str, sheet_class) -> dict[str, bytes]:
    import xlsxwriter
    wb = xlsxwriter.Workbook(path, {"constant_memory": True, "default_date_format": "dd-mmm-yy",
                                    "strings_to_formulas": False, "strings_to_numbers": False,
                                    "strings_to_urls": False, "nan_inf_to_errors": True})
    ws = wb.add_worksheet("T")
    head = wb.add_format({"bold": True, "bg_color": "#C2D8ED"})
    plain = wb.add_format({"font_name": "Calibri", "border": 1})
    num = wb.add_format({"num_format": "#,##0.00", "border": 1})
    day = wb.add_format({"num_format": "DD-MMM-YYYY", "border": 1, "align": "center"})
    ws.write_row(0, 0, ["a", "b", "c", "d", "e"], head)
    ws.set_column(0, 4, 14)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, 5, 4)
    sheet = sheet_class(ws)
    rows = [
        ["plain", 1, 2.5, _dt.datetime(2026, 11, 3), True],
        [" lead & trail ", -3, float("nan"), _dt.date(2026, 1, 2), None],
        ["a<b>c\x0bd_x0041_e\r\nnew", 10 ** 15, 1e-7, None, False],
        ["", 0, math.inf, _dt.datetime(2026, 11, 3), "last"],
        [None, None, None, None, None],
    ]
    for r, values in enumerate(rows, start=1):
        sheet.begin_row(r)
        sheet.text(0, values[0], plain) if isinstance(values[0], str) else sheet.blank(0, plain)
        sheet.number(1, values[1], num) if isinstance(values[1], (int, float)) else sheet.blank(1, plain)
        sheet.auto(2, values[2])
        sheet.date(3, values[3], day) if values[3] is not None else sheet.blank(3, day)
        sheet.auto(4, values[4])
        sheet.end_row()
    close_workbook(wb, [sheet])
    with zipfile.ZipFile(path) as z:
        return {name: z.read(name) for name in z.namelist()}


def enabled() -> bool:
    """Whether the fast path may be used: the switch is not off, and (checked once per process) it
    writes exactly what xlsxwriter writes for a small mixed sample."""
    global _enabled
    if _enabled is not None:
        return _enabled
    if os.environ.get("FAST_XLSX", "1").strip().lower() in ("0", "false", "off", "no"):
        _enabled = False
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            slow = _sample(os.path.join(tmp, "slow.xlsx"), ClassicSheet)
            fast = _sample(os.path.join(tmp, "fast.xlsx"), FastSheet)
        _enabled = slow == fast
        if not _enabled:
            different = [name for name in slow if slow[name] != fast.get(name)]
            logger.warning("fast_xlsx is off: its output differs from xlsxwriter's in %s", different)
    except Exception:       # noqa: BLE001 - any trouble: keep using xlsxwriter itself
        logger.exception("fast_xlsx is off: the self-check failed")
        _enabled = False
    return _enabled


def new_sheet(ws):
    """The row writer for ``ws``: the fast one when it can be trusted, otherwise xlsxwriter's own."""
    if enabled() and getattr(ws, "constant_memory", False) and hasattr(ws, "dim_rowmin"):
        return FastSheet(ws)
    return ClassicSheet(ws)
