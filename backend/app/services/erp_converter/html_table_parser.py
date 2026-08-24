"""Streaming parser for Oracle BI Publisher / Oracle Reports "HTML saved
as .xls" exports.

These files are plain HTML: a small, bounded internal <style> block of
".cN {...}" rules, followed by a <body> of top-level <p> paragraphs
(titles, "Page x of y", filter values) interleaved with <table> elements.
Oracle re-emits a fresh <table> per page/group (which is why a single
logical report can contain thousands of <table> tags and megabytes of
near-duplicate markup), so we never hold more than one top-level table's
subtree in memory at a time - that's what keeps this tractable on
200MB+ files.

Output is a flat, ordered list of Blocks:
  - TextBlock: one non-tabular paragraph line (titles, filter captions)
  - TableBlock: one rectangular grid of CellSpec, with colspan/rowspan
    resolved into a proper row/col grid (Excel merge-ready)

Nested <table> elements (occasionally used for boxed "filter values"
panels) are flattened into their own TableBlock, emitted in document
order immediately before the table that contains them - Excel has no
notion of a table-inside-a-cell, so this is the closest faithful
equivalent.
"""
import re
from lxml import etree

from .css_parser import parse_style_block, resolve_classes
from .io_retry import with_retry

_WS_RE = re.compile(r"[ \t\xa0]+")
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
_CHARSET_RE = re.compile(rb'charset=["\']?([\w-]+)', re.IGNORECASE)


class CellSpec:
    __slots__ = ("row", "col", "rowspan", "colspan", "text", "props",
                 "bold", "italic", "underline", "valign", "wrap")

    def __init__(self, row, col, rowspan, colspan, text, props,
                 bold, italic, underline, valign):
        self.row = row
        self.col = col
        self.rowspan = rowspan
        self.colspan = colspan
        self.text = text
        self.props = props
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.valign = valign
        self.wrap = "\n" in text


class TableBlock:
    def __init__(self):
        self.cells = {}      # (row, col) -> CellSpec (anchor cell only)
        self.nrows = 0
        self.ncols = 0
        self.row_heights = []  # per-row Optional[float] pt


class TextBlock:
    __slots__ = ("text", "props", "bold", "italic", "underline")

    def __init__(self, text, props, bold, italic, underline):
        self.text = text
        self.props = props
        self.bold = bold
        self.italic = italic
        self.underline = underline


def _clean_text(text):
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _tag(elem):
    t = elem.tag
    return t if isinstance(t, str) else None


def _direct_rows(table_elem):
    rows = [c for c in table_elem if _tag(c) == "tr"]
    if rows:
        return rows
    rows = []
    for c in table_elem:
        if _tag(c) in ("thead", "tbody", "tfoot"):
            rows.extend(x for x in c if _tag(x) == "tr")
    return rows


def _parse_pt(value):
    if not value:
        return None
    m = re.match(r"^(-?\d+(?:\.\d+)?)", value.strip())
    if not m:
        return None
    return float(m.group(1))


class _ProgressReader:
    """Wraps a binary file object; reports cumulative bytes read so the
    GUI can show real progress on huge files."""

    def __init__(self, fh, callback, every=1 << 20):
        self._fh = fh
        self._cb = callback
        self._every = every
        self._read = 0
        self._last_report = 0

    def read(self, size=-1):
        chunk = self._fh.read(size)
        self._read += len(chunk)
        if self._cb and self._read - self._last_report >= self._every:
            self._last_report = self._read
            self._cb(self._read)
        return chunk

    def close(self):
        self._fh.close()


class _InlineCtx:
    __slots__ = ("parts", "span_class", "bold", "italic", "underline", "nested")

    def __init__(self):
        self.parts = []
        self.span_class = None
        self.bold = False
        self.italic = False
        self.underline = False
        self.nested = []


class HtmlReportParser:
    def __init__(self):
        self.style_classes = {}
        self._merged_props_cache = {}

    # ---------------------------------------------------------------- style
    def _load_style_block(self, path):
        def _read():
            with open(path, "rb") as fh:
                return fh.read(5_000_000)
        head = with_retry(_read, path, "read")
        encoding = "utf-8"
        m = _CHARSET_RE.search(head)
        if m:
            encoding = m.group(1).decode("ascii", "ignore")
        text = head.decode(encoding, errors="replace")
        m = _STYLE_BLOCK_RE.search(text)
        self.style_classes = parse_style_block(m.group(1)) if m else {}

    def _props(self, class_attr):
        if not class_attr:
            return {}
        return resolve_classes(self.style_classes, class_attr)

    def _merged_props(self, *class_attrs):
        """Resolve and merge several class= strings into one props dict,
        cached by the exact combination of class strings.

        BI Publisher reuses the same small set of CSS classes on every
        row of every repeated per-page/per-group table, so the number of
        distinct (table-class, td-class, span-class) combinations in a
        report is tiny (tens to low hundreds) even when the report has
        millions of cells. Caching the merged dict - and reusing the
        exact same dict object for every cell that shares a combination -
        turns an O(cells) amount of class-parsing/dict-merging work into
        O(distinct combinations), which is the single biggest win for
        multi-million-cell reports. The returned dict must be treated as
        read-only by callers since it is shared.
        """
        cached = self._merged_props_cache.get(class_attrs)
        if cached is not None:
            return cached
        merged = {}
        for ca in class_attrs:
            if ca:
                merged.update(resolve_classes(self.style_classes, ca))
        self._merged_props_cache[class_attrs] = merged
        return merged

    # --------------------------------------------------------------- runs
    def _walk_inline(self, elem, bold, italic, underline, ctx):
        """Recursively collect text/formatting from an inline element tree
        (p, span, b, i, u, br). A nested <table> is diverted to ctx.nested
        instead of being walked as text.
        """
        tag = _tag(elem)
        if tag == "table":
            ctx.nested.append(self._table_to_block(elem))
            return
        if tag == "b":
            bold = True
            ctx.bold = True
        elif tag == "i":
            italic = True
            ctx.italic = True
        elif tag == "u":
            underline = True
            ctx.underline = True
        elif tag == "br":
            ctx.parts.append("\n")
        if tag == "span":
            cls = elem.get("class")
            if cls and ((elem.text or "").strip() or ctx.span_class is None):
                ctx.span_class = cls
        if elem.text:
            ctx.parts.append(elem.text)
        for child in elem:
            self._walk_inline(child, bold, italic, underline, ctx)
            if child.tail:
                ctx.parts.append(child.tail)

    def _extract_inline(self, container):
        """Extract text + formatting from a <p> or <td> element.
        Returns (text, bold, italic, underline, class_name, nested_blocks).
        """
        ctx = _InlineCtx()
        if container.text:
            ctx.parts.append(container.text)
        for child in container:
            self._walk_inline(child, False, False, False, ctx)
            if child.tail:
                ctx.parts.append(child.tail)
        text = _clean_text("".join(ctx.parts))
        return text, ctx.bold, ctx.italic, ctx.underline, ctx.span_class, ctx.nested

    # ------------------------------------------------------------- blocks
    def _paragraph_to_block(self, p_elem):
        text, bold, italic, underline, span_class, nested = self._extract_inline(p_elem)
        block = None
        if text != "":
            props = self._merged_props(p_elem.get("class"), span_class)
            block = TextBlock(text, props, bold, italic, underline)
        return block, nested

    def _table_to_block(self, table_elem):
        block = TableBlock()
        block.pre_blocks = []
        pending = {}  # col -> [remaining_rows, anchor_cell]
        row_index = 0
        table_class = table_elem.get("class")

        for tr in _direct_rows(table_elem):
            col_index = 0
            occupied = set()
            for c, info in list(pending.items()):
                occupied.add(c)
                block.cells[(row_index, c)] = ("span", info[1])
                info[0] -= 1
                if info[0] <= 0:
                    del pending[c]
            saw_cell = False
            for td in tr:
                if _tag(td) not in ("td", "th"):
                    continue
                saw_cell = True
                while col_index in occupied:
                    col_index += 1
                colspan = int((td.get("colspan") or "1").strip() or 1)
                rowspan = int((td.get("rowspan") or "1").strip() or 1)
                text, bold, italic, underline, span_class, nested = self._extract_inline(td)
                if nested:
                    block.pre_blocks.extend(nested)
                props = self._merged_props(table_class, td.get("class"), span_class)
                valign = td.get("valign")
                cell = CellSpec(row_index, col_index, rowspan, colspan, text,
                                 props, bold, italic, underline, valign)
                block.cells[(row_index, col_index)] = cell
                if rowspan > 1:
                    for cc in range(col_index, col_index + colspan):
                        pending[cc] = [rowspan - 1, cell]
                for cc in range(col_index, col_index + colspan):
                    occupied.add(cc)
                    if cc != col_index:
                        block.cells[(row_index, cc)] = ("span", cell)
                col_index += colspan
            block.ncols = max(block.ncols, col_index)
            if saw_cell:
                tr_props = self._props(tr.get("class"))
                block.row_heights.append(_parse_pt(tr_props.get("height")))
                row_index += 1
        block.nrows = row_index
        return block

    # ---------------------------------------------------------------- run
    def parse(self, path, progress_cb=None):
        """Yields Block objects (TextBlock / TableBlock) in document order."""
        self._load_style_block(path)

        raw = with_retry(lambda: open(path, "rb"), path, "read")
        reader = _ProgressReader(raw, progress_cb)
        try:
            ctx = etree.iterparse(reader, events=("start", "end"),
                                   html=True, recover=True, huge_tree=True,
                                   tag=("table", "p"))
            table_depth = 0
            for event, elem in ctx:
                tag = _tag(elem)
                if tag == "table":
                    if event == "start":
                        table_depth += 1
                        continue
                    table_depth -= 1
                    if table_depth == 0:
                        block = self._table_to_block(elem)
                        for pre in block.pre_blocks:
                            yield pre
                        yield block
                        elem.clear(keep_tail=True)
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
                elif tag == "p":
                    if event == "start":
                        continue
                    if table_depth == 0:
                        blk, nested = self._paragraph_to_block(elem)
                        for n in nested:
                            yield n
                        if blk is not None:
                            yield blk
                        elem.clear(keep_tail=True)
                        while elem.getprevious() is not None:
                            del elem.getparent()[0]
        finally:
            reader.close()
