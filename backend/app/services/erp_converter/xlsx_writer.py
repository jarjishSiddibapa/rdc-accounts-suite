"""Writes the ordered Block stream from html_table_parser into a real
.xlsx workbook, preserving fonts, colors, borders, alignment and merged
cells. Uses openpyxl's write-only mode, which is the only mode that
scales to the hundreds-of-thousands of rows these ERP reports can
contain.

Performance note: openpyxl deduplicates style objects (Font/Fill/Border/
Alignment) into shared workbook-level lists via `IndexedList.add()`,
which hashes the *entire* style object on every single `cell.font = ...`
assignment - even when the exact same cached Python object is reused for
millions of cells. Profiling a 430k-cell report showed >70s spent purely
in that repeated hashing. Since ERP reports only ever have a few dozen
distinct style combinations no matter how many rows they have, we bypass
the descriptor machinery: register each distinct Font/Fill/Border/
Alignment/number-format with the workbook exactly once (indices cached
by Python object identity), then write the resulting 9-int StyleArray
onto each cell directly. This is the same internal representation
openpyxl's own descriptors build - just without redoing the expensive
lookup for every cell.
"""
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.utils import get_column_letter
from openpyxl.styles.cell_style import StyleArray
from openpyxl.styles.numbers import BUILTIN_FORMATS_REVERSE, BUILTIN_FORMATS_MAX_SIZE
from openpyxl.worksheet.cell_range import CellRange, MultiCellRange

from .errors import ConversionError
from .style_map import StyleCache, resolve_cell_style
from .value_parser import parse_value
from .html_table_parser import TableBlock, TextBlock
from .io_retry import with_retry

_MAX_COL_WIDTH = 60
_MIN_COL_WIDTH = 8
_MAX_TRACKED_COLS = 500  # safety cap; reports rarely exceed a few dozen columns

# Excel's hard per-sheet limits (XLSX format spec, unrelated to openpyxl).
# write-only mode has no idea these exist and will happily keep writing
# past them, producing a file that Excel then refuses to open cleanly
# ("unreadable content... repair") - checked as we go so a report that
# would exceed the limit fails fast with a clear, actionable message
# instead of silently shipping a corrupt-on-open output.
_MAX_EXCEL_ROWS = 1_048_576
_MAX_EXCEL_COLS = 16_384

_EMPTY_PROPS = {}  # shared singleton so blank/filler cells share one cache identity


class XlsxWriter:
    def __init__(self, progress_cb=None):
        self.wb = Workbook(write_only=True)
        self.ws = self.wb.create_sheet(title="Report")
        self.style_cache = StyleCache()
        self.col_max_len = {}
        self.current_row = 0
        self.merges = []
        self._wrote_any = False
        self.progress_cb = progress_cb

        # id(openpyxl style object) -> registered index in the workbook's
        # shared style list. Safe because our StyleCache keeps every such
        # object alive for the lifetime of this writer, so ids never get
        # reused for something else in the meantime.
        self._font_ids = {}
        self._fill_ids = {}
        self._border_ids = {}
        self._align_ids = {}
        self._numfmt_ids = {}

        # A resolved-style *bundle* (font/fill/border/alignment index
        # tuple) keyed by (id(props_dict), bold, italic, underline,
        # valign, wrap) - props dicts are themselves de-duplicated and
        # cached upstream by the parser, so this collapses the huge
        # majority of cells in a report down to a handful of distinct
        # style computations.
        self._bundle_cache = {}

        # (font_idx, fill_idx, border_idx, numfmt_idx, align_idx) -> the one
        # StyleArray instance for that exact combination. ERP reports only
        # ever have a handful of distinct combinations, so reusing the same
        # object for every matching cell (instead of allocating a fresh
        # 9-int array per cell) cuts allocation/GC pressure across the
        # million-cell reports these files can contain.
        self._style_array_cache = {}

    # ------------------------------------------------------------ id-based interning
    def _reg(self, cache, coll, obj):
        if obj is None:
            return 0
        key = id(obj)
        idx = cache.get(key)
        if idx is None:
            idx = coll.add(obj)
            cache[key] = idx
        return idx

    def _numfmt_index(self, fmt):
        if not fmt:
            return 0
        idx = self._numfmt_ids.get(fmt)
        if idx is not None:
            return idx
        if fmt in BUILTIN_FORMATS_REVERSE:
            idx = BUILTIN_FORMATS_REVERSE[fmt]
        else:
            idx = self.wb._number_formats.add(fmt) + BUILTIN_FORMATS_MAX_SIZE
        self._numfmt_ids[fmt] = idx
        return idx

    def _style_bundle(self, props, bold, italic, underline, valign, wrap):
        key = (id(props), bold, italic, underline, valign, wrap)
        bundle = self._bundle_cache.get(key)
        if bundle is not None:
            return bundle
        style = resolve_cell_style(props, bold=bold, italic=italic,
                                    underline=underline, valign=valign, wrap=wrap)
        font = self.style_cache.font(style)
        fill = self.style_cache.fill(style)
        border = self.style_cache.border(style)
        align = self.style_cache.alignment(style)
        if not (align.horizontal or align.vertical or align.wrap_text):
            align = None
        font_idx = self._reg(self._font_ids, self.wb._fonts, font)
        fill_idx = self._reg(self._fill_ids, self.wb._fills, fill)
        border_idx = self._reg(self._border_ids, self.wb._borders, border)
        align_idx = self._reg(self._align_ids, self.wb._alignments, align)
        bundle = (font_idx, fill_idx, border_idx, align_idx)
        self._bundle_cache[key] = bundle
        return bundle

    # ------------------------------------------------------------ helpers
    def _make_cell(self, value, numfmt, props, bold=False, italic=False,
                    underline=False, valign=None, wrap=False):
        cell = WriteOnlyCell(self.ws, value=value)
        font_idx, fill_idx, border_idx, align_idx = self._style_bundle(
            props, bold, italic, underline, valign, wrap)
        numfmt_idx = self._numfmt_index(numfmt)
        if font_idx or fill_idx or border_idx or align_idx or numfmt_idx:
            style_key = (font_idx, fill_idx, border_idx, numfmt_idx, align_idx)
            style_array = self._style_array_cache.get(style_key)
            if style_array is None:
                style_array = StyleArray(
                    [font_idx, fill_idx, border_idx, numfmt_idx, 0, align_idx, 0, 0, 0])
                self._style_array_cache[style_key] = style_array
            cell._style = style_array
        return cell

    def _track_width(self, col_index, length):
        if col_index >= _MAX_TRACKED_COLS:
            return
        cur = self.col_max_len.get(col_index, 0)
        if length > cur:
            self.col_max_len[col_index] = length

    def _spacer_if_needed(self):
        if self._wrote_any:
            self._check_row_capacity()
            self.ws.append([])
            self.current_row += 1
        self._wrote_any = True

    def _check_row_capacity(self):
        if self.current_row + 1 > _MAX_EXCEL_ROWS:
            raise ConversionError(
                f"This report converts to more than {_MAX_EXCEL_ROWS:,} rows, "
                "which exceeds Excel's per-sheet limit. Split the source "
                "export into smaller date ranges or batches and convert "
                "each one separately."
            )

    def _check_col_capacity(self, ncols):
        if ncols > _MAX_EXCEL_COLS:
            raise ConversionError(
                f"This report has more than {_MAX_EXCEL_COLS:,} columns, "
                "which exceeds Excel's per-sheet limit."
            )

    # ------------------------------------------------------------- write
    def write_blocks(self, blocks_iter):
        for blk in blocks_iter:
            if isinstance(blk, TextBlock):
                self._write_text_block(blk)
            else:
                self._write_table_block(blk)
            if self.progress_cb:
                self.progress_cb(self.current_row)

    def _write_text_block(self, blk):
        if blk.text == "":
            return
        self._spacer_if_needed()
        self._check_row_capacity()
        cell = self._make_cell(blk.text, None, blk.props, bold=blk.bold,
                                italic=blk.italic, underline=blk.underline,
                                wrap="\n" in blk.text)
        self.ws.append([cell])
        self.current_row += 1
        longest = max((len(ln) for ln in blk.text.split("\n")), default=0)
        self._track_width(0, longest)

    def _write_table_block(self, blk: TableBlock):
        if blk.nrows == 0 or blk.ncols == 0:
            return
        self._check_col_capacity(blk.ncols)
        if self.current_row + blk.nrows > _MAX_EXCEL_ROWS:
            raise ConversionError(
                f"This report converts to more than {_MAX_EXCEL_ROWS:,} rows, "
                "which exceeds Excel's per-sheet limit. Split the source "
                "export into smaller date ranges or batches and convert "
                "each one separately."
            )
        self._spacer_if_needed()
        base_row = self.current_row  # rows already written before this block (0-based)
        for r in range(blk.nrows):
            row_cells = []
            for c in range(blk.ncols):
                entry = blk.cells.get((r, c))
                if entry is None:
                    row_cells.append(self._make_cell(None, None, _EMPTY_PROPS))
                    continue
                if isinstance(entry, tuple):  # ("span", anchor_cell) - merge continuation
                    anchor = entry[1]
                    row_cells.append(self._make_cell(
                        None, None, anchor.props, bold=anchor.bold,
                        italic=anchor.italic, underline=anchor.underline,
                        valign=anchor.valign))
                    continue
                cell_spec = entry
                value, numfmt = parse_value(cell_spec.text)
                row_cells.append(self._make_cell(
                    value, numfmt, cell_spec.props, bold=cell_spec.bold,
                    italic=cell_spec.italic, underline=cell_spec.underline,
                    valign=cell_spec.valign, wrap=cell_spec.wrap))
                text_len = len(str(value)) if value is not None else 0
                self._track_width(c, text_len)
                if cell_spec.rowspan > 1 or cell_spec.colspan > 1:
                    r1 = base_row + r + 1
                    c1 = c + 1
                    r2 = r1 + cell_spec.rowspan - 1
                    c2 = c1 + cell_spec.colspan - 1
                    self.merges.append((r1, c1, r2, c2))
            self.ws.append(row_cells)
            self.current_row += 1
            rh = blk.row_heights[r] if r < len(blk.row_heights) else None
            if rh:
                self.ws.row_dimensions[base_row + r + 1].height = rh

    # ------------------------------------------------------------- finish
    def finalize(self, output_path):
        # WriteOnlyWorksheet doesn't inherit merge_cells()/self.merged_cells
        # from the regular Worksheet class (it's absent from the small set
        # of methods write-only mode copies over) - calling ws.merge_cells()
        # here has always raised AttributeError, silently swallowed by a
        # bare except, so colspan/rowspan cells were never actually merged
        # in any converted output. WorksheetWriter.write_tail() does read
        # ws.merged_cells if present, so setting it directly (bypassing the
        # missing method) is enough to make merges reach the file.
        if self.merges:
            self.ws.merged_cells = MultiCellRange(
                CellRange(min_col=c1, min_row=r1, max_col=c2, max_row=r2)
                for (r1, c1, r2, c2) in self.merges
            )
        for col_index, length in self.col_max_len.items():
            width = max(_MIN_COL_WIDTH, min(_MAX_COL_WIDTH, length + 2))
            self.ws.column_dimensions[get_column_letter(col_index + 1)].width = width
        with_retry(lambda: self.wb.save(output_path), output_path, "write")
