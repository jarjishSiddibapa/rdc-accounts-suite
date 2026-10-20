"""Write an .xlsx report as an .xlsb (Excel Binary Workbook), in pure Python.

.xlsb holds exactly the same sheets, values, formulas and formats as the .xlsx
in a much smaller file (the cells are binary records instead of XML), which is
what lets a report that is over the email limit go out as one attachment. No
Excel, no COM and no other software is involved: this reads the finished .xlsx
and writes the binary records itself ([MS-XLSB] / BIFF12).

It is made for the workbooks this application writes with openpyxl (text,
numbers, dates as numbers, plain formulas, fonts / fills / borders /
alignment / number formats, column widths, row heights, a frozen header row,
an autofilter, tab colours). Anything outside that raises ``Unsupported`` and
the caller falls back to something that loses nothing, so a workbook is never
converted into something different from what was built.

The sheets are processed as a stream, so memory stays small however many rows
there are; only the table of distinct strings is kept.
"""

from __future__ import annotations

import math
import re
import shutil
import struct
import tempfile
import zipfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_MAX_COLS = 16384
_MAX_ROWS = 1048576
_EVAL_CELL_LIMIT = 300_000          # cells remembered to work out formula results


class Unsupported(Exception):
    """The workbook uses something this writer does not convert."""


# ---------------------------------------------------------------------------
# record encoding
# ---------------------------------------------------------------------------

def _record(record_id: int, payload: bytes = b"") -> bytes:
    size = len(payload)
    if record_id < 0x80:
        head = bytes((record_id,))
    else:
        head = bytes(((record_id & 0x7F) | 0x80, record_id >> 7))
    sizes = bytearray()
    while True:
        low = size & 0x7F
        size >>= 7
        if size:
            sizes.append(low | 0x80)
        else:
            sizes.append(low)
            break
    return head + bytes(sizes) + payload


def _wide(text: str) -> bytes:
    """XLWideString: length in UTF-16 units, then the characters."""
    data = text.encode("utf-16-le", "surrogatepass")
    return struct.pack("<I", len(data) // 2) + data


_ESCAPED = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _unescape(text: str) -> str:
    """xlsx writes control characters as _x000D_; the binary file holds them as is."""
    if "_x" not in text:
        return text
    return _ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), text)


def _flag(element) -> bool:
    if element is None:
        return False
    return element.get("val", "1").lower() not in ("0", "false")


# ---------------------------------------------------------------------------
# styles.xml -> styles.bin
# ---------------------------------------------------------------------------

_AUTO_COLOR = bytes((0x01, 0x40, 0, 0, 0, 0, 0, 0))
_NO_COLOR = bytes((0x01, 0x00, 0, 0, 0, 0, 0, 0))
_FILL_FORE_DEFAULT = bytes((0x03, 0x40, 0, 0, 0, 0, 0, 0xFF))
_FILL_BACK_DEFAULT = bytes((0x03, 0x41, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF))

_PATTERNS = {
    "none": 0, "solid": 1, "mediumGray": 2, "darkGray": 3, "lightGray": 4,
    "darkHorizontal": 5, "darkVertical": 6, "darkDown": 7, "darkUp": 8,
    "darkGrid": 9, "darkTrellis": 10, "lightHorizontal": 11, "lightVertical": 12,
    "lightDown": 13, "lightUp": 14, "lightGrid": 15, "lightTrellis": 16,
    "gray125": 17, "gray0625": 18,
}
_BORDER_STYLES = {
    None: 0, "none": 0, "thin": 1, "medium": 2, "dashed": 3, "dotted": 4, "thick": 5,
    "double": 6, "hair": 7, "mediumDashed": 8, "dashDot": 9, "mediumDashDot": 10,
    "dashDotDot": 11, "mediumDashDotDot": 12, "slantDashDot": 13,
}
_HORIZONTAL = {
    None: 0, "general": 0, "left": 1, "center": 2, "right": 3, "fill": 4,
    "justify": 5, "centerContinuous": 6, "distributed": 7,
}
_VERTICAL = {None: 2, "bottom": 2, "top": 0, "center": 1, "justify": 3, "distributed": 4}
_UNDERLINE = {"single": 1, "double": 2, "singleAccounting": 0x21, "doubleAccounting": 0x22}


def _color(element, default: bytes) -> bytes:
    """BrtColor (8 bytes) from an xlsx colour element."""
    if element is None:
        return default
    tint = int(round(float(element.get("tint", "0")) * 32767))
    if element.get("rgb") is not None:
        argb = element.get("rgb")
        if len(argb) == 6:
            argb = "FF" + argb
        red, green, blue = int(argb[2:4], 16), int(argb[4:6], 16), int(argb[6:8], 16)
        return struct.pack("<BBhBBBB", 0x05, 0xFF, tint, red, green, blue, 0xFF)
    if element.get("theme") is not None:
        return struct.pack("<BBhBBBB", 0x07, int(element.get("theme")), tint, 0, 0, 0, 0xFF)
    if element.get("indexed") is not None:
        return struct.pack("<BBhBBBB", 0x03, int(element.get("indexed")), tint, 0xFF, 0xFF, 0xFF, 0xFF)
    if element.get("auto") is not None:
        return _AUTO_COLOR
    return default


def _font(font) -> bytes:
    if font.find(_MAIN + "outline") is not None or font.find(_MAIN + "shadow") is not None \
            or font.find(_MAIN + "condense") is not None or font.find(_MAIN + "extend") is not None:
        raise Unsupported("font effects")
    size = font.find(_MAIN + "sz")
    height = int(round(float(size.get("val")) * 20)) if size is not None else 220
    bold = _flag(font.find(_MAIN + "b"))
    grbit = (0x01 if bold else 0) | (0x02 if _flag(font.find(_MAIN + "i")) else 0) \
        | (0x08 if _flag(font.find(_MAIN + "strike")) else 0)
    underline = font.find(_MAIN + "u")
    uls = 0 if underline is None else _UNDERLINE.get(underline.get("val", "single"), 1)
    vertical = font.find(_MAIN + "vertAlign")
    sss = {"superscript": 1, "subscript": 2}.get(vertical.get("val") if vertical is not None else None, 0)
    family = font.find(_MAIN + "family")
    charset = font.find(_MAIN + "charset")
    scheme = font.find(_MAIN + "scheme")
    scheme_code = {"major": 1, "minor": 2}.get(scheme.get("val") if scheme is not None else None, 0)
    name = font.find(_MAIN + "name")
    return (
        struct.pack("<HHHHBBBB", height, grbit, 700 if bold else 400, sss, uls,
                    int(family.get("val")) if family is not None else 0,
                    int(charset.get("val")) if charset is not None else 0, 0)
        + _color(font.find(_MAIN + "color"), _AUTO_COLOR)
        + bytes((scheme_code,))
        + _wide(name.get("val") if name is not None else "Calibri")
    )


def _fill(fill) -> bytes:
    pattern = fill.find(_MAIN + "patternFill")
    if pattern is None:
        raise Unsupported("gradient fill")
    kind = _PATTERNS.get(pattern.get("patternType") or "none")
    if kind is None:
        raise Unsupported("fill pattern")
    return (
        struct.pack("<I", kind)
        + _color(pattern.find(_MAIN + "fgColor"), _FILL_FORE_DEFAULT)
        + _color(pattern.find(_MAIN + "bgColor"), _FILL_BACK_DEFAULT)
        + bytes(48)
    )


def _border(border) -> bytes:
    def side(name: str) -> bytes:
        element = border.find(_MAIN + name)
        style = element.get("style") if element is not None else None
        if style not in _BORDER_STYLES:
            raise Unsupported("border style")
        color = _color(element.find(_MAIN + "color") if element is not None else None, _NO_COLOR)
        if style and element.find(_MAIN + "color") is None:
            color = _AUTO_COLOR
        return struct.pack("<H", _BORDER_STYLES[style]) + color

    flags = (0x01 if border.get("diagonalDown") in ("1", "true") else 0) \
        | (0x02 if border.get("diagonalUp") in ("1", "true") else 0)
    return bytes((flags,)) + side("top") + side("bottom") + side("left") + side("right") + side("diagonal")


def _xf(xf, parent: int) -> bytes:
    alignment = xf.find(_MAIN + "alignment")
    protection = xf.find(_MAIN + "protection")
    horizontal = vertical = rotation = indent = 0
    wrap = shrink = False
    reading = 0
    if alignment is not None:
        horizontal = _HORIZONTAL.get(alignment.get("horizontal"))
        vertical = _VERTICAL.get(alignment.get("vertical"))
        if horizontal is None or vertical is None:
            raise Unsupported("alignment")
        rotation = int(alignment.get("textRotation", 0))
        indent = int(alignment.get("indent", 0))
        wrap = alignment.get("wrapText") in ("1", "true")
        shrink = alignment.get("shrinkToFit") in ("1", "true")
        reading = int(alignment.get("readingOrder", 0))
    else:
        vertical = 2
    locked = protection is None or protection.get("locked", "1") not in ("0", "false")
    hidden = protection is not None and protection.get("hidden") in ("1", "true")
    number, font, fill, border = (int(xf.get(k, 0)) for k in ("numFmtId", "fontId", "fillId", "borderId"))
    flags = (
        horizontal | (vertical << 3) | (0x40 if wrap else 0) | (0x100 if shrink else 0) | (reading << 10)
        | (0x1000 if locked else 0) | (0x2000 if hidden else 0)
        | (0x4000 if xf.get("pivotButton") in ("1", "true") else 0)
        | (0x8000 if xf.get("quotePrefix") in ("1", "true") else 0)
    )
    applies = (
        (0x01 if number else 0) | (0x02 if font else 0) | (0x04 if fill else 0) | (0x08 if border else 0)
        | (0x10 if alignment is not None else 0) | (0x20 if protection is not None else 0)
    )
    return struct.pack("<HHHHHBBHH", parent, number, font, fill, border, rotation, indent, flags, applies)


def _styles(styles_xml: bytes) -> tuple[bytes, int]:
    """styles.bin, and how many cell formats (xf) there are."""
    root = ET.fromstring(styles_xml)
    out = [_record(278)]

    formats = root.find(_MAIN + "numFmts")
    custom = [] if formats is None else list(formats)
    out.append(_record(615, struct.pack("<I", len(custom))))
    for item in custom:
        out.append(_record(44, struct.pack("<H", int(item.get("numFmtId"))) + _wide(item.get("formatCode"))))
    out.append(_record(616))

    fonts = list(root.find(_MAIN + "fonts"))
    out.append(_record(611, struct.pack("<I", len(fonts))))
    out.extend(_record(43, _font(font)) for font in fonts)
    out.append(_record(612))

    fills = list(root.find(_MAIN + "fills"))
    out.append(_record(603, struct.pack("<I", len(fills))))
    out.extend(_record(45, _fill(fill)) for fill in fills)
    out.append(_record(604))

    borders = list(root.find(_MAIN + "borders"))
    out.append(_record(613, struct.pack("<I", len(borders))))
    out.extend(_record(46, _border(border)) for border in borders)
    out.append(_record(614))

    style_xfs = list(root.find(_MAIN + "cellStyleXfs"))
    out.append(_record(626, struct.pack("<I", len(style_xfs))))
    out.extend(_record(47, _xf(xf, 0xFFFF)) for xf in style_xfs)
    out.append(_record(627))

    cell_xfs = list(root.find(_MAIN + "cellXfs"))
    out.append(_record(617, struct.pack("<I", len(cell_xfs))))
    out.extend(_record(47, _xf(xf, int(xf.get("xfId", 0)))) for xf in cell_xfs)
    out.append(_record(618))

    named = root.find(_MAIN + "cellStyles")
    named = [] if named is None else list(named)
    out.append(_record(619, struct.pack("<I", len(named))))
    for style in named:
        builtin = style.get("builtinId")
        out.append(_record(48, struct.pack(
            "<IHBB", int(style.get("xfId", 0)), 1 if builtin is not None else 0,
            int(builtin) if builtin is not None else 0, 0xFF if builtin is not None else 0,
        ) + _wide(style.get("name"))))
    out.append(_record(620))

    differential = root.find(_MAIN + "dxfs")
    if differential is not None and len(differential):
        raise Unsupported("conditional formatting styles")
    out.append(_record(505, struct.pack("<I", 0)))
    out.append(_record(506))
    out.append(_record(508, struct.pack("<I", 0) + _wide("TableStyleMedium9") + _wide("PivotStyleLight16")))
    out.append(_record(509))
    out.append(_record(279))
    return b"".join(out), len(cell_xfs)


# ---------------------------------------------------------------------------
# formulas: text -> binary tokens (ptg) and a value for the cached result
# ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+)"
    r"|(?P<func>[A-Za-z][A-Za-z0-9_.]*)\s*\("
    r"|(?P<ref>\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)"
    r"|(?P<op>[-+*/(),]))"
)
_CELL = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)")

# name -> (function number, fewest args, most args, argument classes, fixed args)
# a class is "V" (value) or "R" (reference); the last one repeats
_FUNCTIONS = {
    "ROUND": (27, 2, 2, "VV", True),
    "ABS": (24, 1, 1, "V", True),
    "SUM": (4, 1, 255, "R", False),
    "SUBTOTAL": (344, 2, 255, "VR", False),
}


def _column_index(letters: str) -> int:
    number = 0
    for letter in letters.upper():
        number = number * 26 + (ord(letter) - 64)
    return number - 1


def _parse_formula(text: str):
    tokens = []
    position = 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise Unsupported(f"formula {text!r}")
        position = match.end()
        for kind in ("num", "func", "ref", "op"):
            if match.group(kind) is not None:
                tokens.append((kind, match.group(kind)))
                break
    index = 0

    def peek():
        return tokens[index] if index < len(tokens) else (None, None)

    def take():
        nonlocal index
        index += 1
        return tokens[index - 1]

    def expression():
        node = term()
        while peek() in (("op", "+"), ("op", "-")):
            node = ("bin", take()[1], node, term())
        return node

    def term():
        node = unary()
        while peek() in (("op", "*"), ("op", "/")):
            node = ("bin", take()[1], node, unary())
        return node

    def unary():
        if peek() == ("op", "-"):
            take()
            return ("neg", unary())
        if peek() == ("op", "+"):
            take()
            return unary()
        return primary()

    def primary():
        kind, value = peek()
        if kind is None:
            raise Unsupported("formula ends early")
        take()
        if kind == "num":
            return ("num", float(value))
        if kind == "ref":
            return _reference(value)
        if kind == "func":
            name = value.upper()
            if name not in _FUNCTIONS:
                raise Unsupported(f"function {name}")
            args = []
            if peek() != ("op", ")"):
                args.append(expression())
                while peek() == ("op", ","):
                    take()
                    args.append(expression())
            if take() != ("op", ")"):
                raise Unsupported("formula brackets")
            low, high = _FUNCTIONS[name][1], _FUNCTIONS[name][2]
            if not low <= len(args) <= high:
                raise Unsupported(f"{name} arguments")
            return ("call", name, args)
        if (kind, value) == ("op", "("):
            node = expression()
            if take() != ("op", ")"):
                raise Unsupported("formula brackets")
            return ("paren", node)
        raise Unsupported("formula")

    tree = expression()
    if index != len(tokens):
        raise Unsupported("formula")
    return tree


def _reference(text: str):
    cells = _CELL.findall(text)
    parts = []
    for col_abs, letters, row_abs, digits in cells:
        col, row = _column_index(letters), int(digits) - 1
        if not (0 <= col < _MAX_COLS and 0 <= row < _MAX_ROWS):
            raise Unsupported("reference out of range")
        parts.append((row, col, not col_abs, not row_abs))
    if len(parts) == 1:
        return ("ref", *parts[0])
    return ("area", parts[0], parts[1])


def _colfield(col: int, col_relative: bool, row_relative: bool) -> int:
    return col | (0x4000 if col_relative else 0) | (0x8000 if row_relative else 0)


def _compile(node, klass: str) -> bytes:
    kind = node[0]
    if kind == "num":
        value = node[1]
        if value == int(value) and 0 <= value <= 65535 and not (value == 0 and math.copysign(1, value) < 0):
            return b"\x1e" + struct.pack("<H", int(value))
        return b"\x1f" + struct.pack("<d", value)
    if kind == "ref":
        _, row, col, col_rel, row_rel = node
        return bytes((0x44 if klass == "V" else 0x24,)) + struct.pack("<IH", row, _colfield(col, col_rel, row_rel))
    if kind == "area":
        (row1, col1, c1_rel, r1_rel), (row2, col2, c2_rel, r2_rel) = node[1], node[2]
        return bytes((0x45 if klass == "V" else 0x25,)) + struct.pack(
            "<IIHH", min(row1, row2), max(row1, row2),
            _colfield(min(col1, col2), c1_rel, r1_rel), _colfield(max(col1, col2), c2_rel, r2_rel),
        )
    if kind == "neg":
        return _compile(node[1], "V") + b"\x13"
    if kind == "paren":
        return _compile(node[1], klass) + b"\x15"
    if kind == "bin":
        return _compile(node[2], "V") + _compile(node[3], "V") + {"+": b"\x03", "-": b"\x04", "*": b"\x05", "/": b"\x06"}[node[1]]
    if kind == "call":
        number, _, _, classes, fixed = _FUNCTIONS[node[1]]
        body = b"".join(_compile(arg, classes[min(i, len(classes) - 1)]) for i, arg in enumerate(node[2]))
        if fixed:
            return body + b"\x41" + struct.pack("<H", number)
        return body + b"\x42" + bytes((len(node[2]),)) + struct.pack("<H", number)
    raise Unsupported("formula")


class _Err:
    """An Excel error result (such as #VALUE!) while working out a cached value."""
    CODES = {"#NULL!": 0, "#DIV/0!": 7, "#VALUE!": 15, "#REF!": 23, "#NAME?": 29, "#NUM!": 36, "#N/A": 42}

    def __init__(self, name: str):
        self.name = name


def _number(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _Err):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return _Err("#VALUE!")


def _evaluate(node, cells: dict):
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "ref":
        return _number(cells.get((node[1], node[2])))
    if kind == "paren":
        return _evaluate(node[1], cells)
    if kind == "neg":
        value = _evaluate(node[1], cells)
        return value if isinstance(value, _Err) else -value
    if kind == "bin":
        left, right = _evaluate(node[2], cells), _evaluate(node[3], cells)
        for side in (left, right):
            if isinstance(side, _Err):
                return side
        if node[1] == "+":
            return left + right
        if node[1] == "-":
            return left - right
        if node[1] == "*":
            return left * right
        return left / right if right else _Err("#DIV/0!")
    if kind == "call":
        name, args = node[1], node[2]
        if name in ("SUM", "SUBTOTAL"):
            total = 0.0
            for arg in args[1 if name == "SUBTOTAL" else 0:]:
                if arg[0] == "area":
                    (row1, col1, *_), (row2, col2, *_) = arg[1], arg[2]
                    for row in range(min(row1, row2), max(row1, row2) + 1):
                        for col in range(min(col1, col2), max(col1, col2) + 1):
                            value = cells.get((row, col))
                            if isinstance(value, _Err):
                                return value
                            if isinstance(value, (int, float)):
                                total += value
                else:
                    value = _evaluate(arg, cells)
                    if isinstance(value, _Err):
                        return value
                    total += value
            return total
        value = _evaluate(args[0], cells)
        if isinstance(value, _Err):
            return value
        if name == "ABS":
            return abs(value)
        digits = _evaluate(args[1], cells)
        if isinstance(digits, _Err):
            return digits
        quantum = Decimal(1).scaleb(-int(digits))
        return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP)) if value == value else value
    raise Unsupported("formula")


# ---------------------------------------------------------------------------
# worksheets
# ---------------------------------------------------------------------------

_WS_PROP_FLAGS = bytes.fromhex("c90402")
_WS_VIEW = bytearray.fromhex("980300000000000000000000000040000000640000000000000000000000")
_WS_FMT_INFO = bytes.fromhex("ffffffff08002c0100000000")
_MARGINS = struct.pack("<6d", 0.75, 0.75, 1.0, 1.0, 0.5, 0.5)
_ERRORS = _Err.CODES


class _Strings:
    """The workbook's table of distinct strings."""

    def __init__(self):
        self.index: dict[str, int] = {}
        self.total = 0

    def add(self, text: str) -> int:
        self.total += 1
        found = self.index.get(text)
        if found is None:
            found = self.index[text] = len(self.index)
        return found

    def records(self) -> bytes:
        parts = [_record(159, struct.pack("<II", self.total, len(self.index)))]
        parts.extend(_record(19, b"\x00" + _wide(text)) for text in self.index)
        parts.append(_record(160))
        return b"".join(parts)


class _Sheet:
    def __init__(self, name: str, state: str):
        self.name = name
        self.state = state
        self.tab_color = bytes((0x00, 0x40, 0, 0, 0, 0, 0, 0))
        self.show_grid = True
        self.frozen_rows = 0
        self.columns: list[bytes] = []
        self.filter: tuple[int, int, int, int] | None = None
        self.first_row = self.first_col = None
        self.last_row = self.last_col = 0
        self.selected = False


def _split_range(ref: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?", ref)
    if not match:
        raise Unsupported("autofilter range")
    col1, row1 = _column_index(match.group(1)), int(match.group(2)) - 1
    col2 = _column_index(match.group(3)) if match.group(3) else col1
    row2 = int(match.group(4)) - 1 if match.group(4) else row1
    return row1, row2, col1, col2


def _cell_number(value: float, style: bytes, col: int) -> bytes:
    """A number as the smallest record that holds it exactly."""
    if not math.isfinite(value):
        raise Unsupported("number that is not finite")
    if value == 0:
        value = 0.0                                   # Excel does not keep a negative zero
    head = struct.pack("<I", col) + style
    if abs(value) < 2 ** 29 and value == int(value):
        return b"" + head + struct.pack("<I", ((int(value) << 2) | 2) & 0xFFFFFFFF)
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    if bits & 0xFFFFFFFF == 0 and (bits >> 32) & 3 == 0:
        return b"" + head + struct.pack("<I", bits >> 32)
    return b"" + head + struct.pack("<d", value)


class _SheetReader:
    """Parser target for one worksheet's XML: turns each row into records
    while the XML streams past, and notes the sheet's layout."""

    _IGNORED = (
        "worksheet", "sheetPr", "outlinePr", "dimension", "sheetViews", "selection", "cols",
        "sheetData", "is",
    )

    def __init__(self, sheet: _Sheet, strings: _Strings, shared: list[str], style_count: int, body):
        self.sheet = sheet
        self.strings = strings
        self.shared = shared
        self.style_count = style_count
        self.body = body
        self.styles = [struct.pack("<I", i)[:3] + b"\x00" for i in range(style_count)]
        self.buffer = bytearray()
        self.known: dict[tuple[int, int], object] = {}
        self.remembering = True
        self.formulas: dict[str, tuple] = {}
        self.margins = _MARGINS
        self.row = -1
        self.row_attrs: dict = {}
        self.cells: list[bytes] = []
        self.first_col = self.last_col = 0
        self.col = -1
        self.cell_attrs: dict = {}
        self.value = self.formula = None
        self.inline: list[str] = []
        self.capture: list[str] | None = None
        self.starts = {
            _MAIN + "row": self._start_row,
            _MAIN + "c": self._start_cell,
            _MAIN + "v": self._start_value,
            _MAIN + "f": self._start_formula,
            _MAIN + "t": self._start_text,
            _MAIN + "tabColor": self._start_tab_color,
            _MAIN + "sheetView": self._start_view,
            _MAIN + "pane": self._start_pane,
            _MAIN + "sheetFormatPr": self._start_format,
            _MAIN + "col": self._start_col,
            _MAIN + "autoFilter": self._start_filter,
            _MAIN + "pageMargins": self._start_margins,
            _MAIN + "pageSetUpPr": self._start_page_setup,
        }
        for name in self._IGNORED:
            self.starts[_MAIN + name] = self._nothing
        self.ends = {
            _MAIN + "row": self._end_row,
            _MAIN + "c": self._end_cell,
            _MAIN + "v": self._end_capture,
            _MAIN + "f": self._end_capture,
            _MAIN + "t": self._end_capture,
        }

    # -- expat target interface --------------------------------------------
    def start(self, tag, attrib):
        handler = self.starts.get(tag)
        if handler is None:
            raise Unsupported(f"worksheet element {tag.rsplit('}', 1)[-1]}")
        handler(attrib)

    def end(self, tag):
        handler = self.ends.get(tag)
        if handler is not None:
            handler()

    def data(self, text):
        if self.capture is not None:
            self.capture.append(text)

    def close(self):
        self._flush()

    # -- layout --------------------------------------------------------------
    def _nothing(self, attrib):
        pass

    def _start_tab_color(self, attrib):
        self.sheet.tab_color = _color(attrib, self.sheet.tab_color)

    def _start_view(self, attrib):
        extra = set(attrib) - {"showGridLines", "tabSelected", "workbookViewId", "zoomScale", "zoomScaleNormal"}
        if extra or attrib.get("zoomScale", "100") != "100":
            raise Unsupported("sheet view")
        self.sheet.show_grid = attrib.get("showGridLines", "1") not in ("0", "false")

    def _start_pane(self, attrib):
        split = float(attrib.get("ySplit", 0))
        if attrib.get("xSplit") not in (None, "0") or attrib.get("state") != "frozen" or split <= 0 \
                or split != int(split) or attrib.get("topLeftCell") != f"A{int(split) + 1}":
            raise Unsupported("pane")
        self.sheet.frozen_rows = int(split)

    def _start_format(self, attrib):
        if attrib.get("defaultColWidth") or float(attrib.get("defaultRowHeight", 15)) != 15 \
                or attrib.get("zeroHeight") in ("1", "true"):
            raise Unsupported("sheet format")

    def _start_col(self, attrib):
        if attrib.get("style") not in (None, "0"):
            raise Unsupported("column format")
        first, last = int(attrib["min"]) - 1, int(attrib["max"]) - 1
        if not (0 <= first <= last < _MAX_COLS):
            raise Unsupported("column range")
        flags = (0x01 if attrib.get("hidden") in ("1", "true") else 0) \
            | (0x02 if attrib.get("customWidth") in ("1", "true") else 0) \
            | (0x04 if attrib.get("bestFit") in ("1", "true") else 0) \
            | (int(attrib.get("outlineLevel", 0)) << 8) \
            | (0x1000 if attrib.get("collapsed") in ("1", "true") else 0)
        width = int(round(float(attrib.get("width", 0)) * 256))
        self.sheet.columns.append(_record(60, struct.pack("<IIIIH", first, last, width, 0, flags)))

    def _start_filter(self, attrib):
        self.sheet.filter = _split_range(attrib["ref"])

    def _start_margins(self, attrib):
        self.margins = struct.pack("<6d", *(float(attrib[k]) for k in ("left", "right", "top", "bottom", "header", "footer")))

    def _start_page_setup(self, attrib):
        if attrib:
            raise Unsupported("page setup")

    # -- rows and cells ------------------------------------------------------
    def _start_row(self, attrib):
        self.row = int(attrib["r"]) - 1 if attrib.get("r") else self.row + 1
        if not 0 <= self.row < _MAX_ROWS:
            raise Unsupported("row out of range")
        if attrib.get("customFormat") in ("1", "true") or attrib.get("s") not in (None, "0"):
            raise Unsupported("row format")
        self.row_attrs = attrib
        self.cells = []
        self.col = -1
        self.first_col = None

    def _end_row(self):
        attrs = self.row_attrs
        custom = attrs.get("customHeight") in ("1", "true") and attrs.get("ht") is not None
        hidden = attrs.get("hidden") in ("1", "true")
        outline = int(attrs.get("outlineLevel", 0))
        if not (self.cells or custom or hidden or outline):
            return
        height = int(round(float(attrs["ht"]) * 20)) if custom else 300
        flags = outline | (0x08 if attrs.get("collapsed") in ("1", "true") else 0) \
            | (0x10 if hidden else 0) | (0x20 if custom else 0)
        header = struct.pack("<IIH", self.row, 0, height) + bytes((0, flags, 0))
        if self.cells:
            header += struct.pack("<III", 1, self.first_col, self.last_col)
            sheet = self.sheet
            sheet.first_row = self.row if sheet.first_row is None else min(sheet.first_row, self.row)
            sheet.last_row = max(sheet.last_row, self.row)
            sheet.first_col = self.first_col if sheet.first_col is None else min(sheet.first_col, self.first_col)
            sheet.last_col = max(sheet.last_col, self.last_col)
        else:
            header += struct.pack("<I", 0)
        self.buffer += _record(0, header)
        self.buffer += b"".join(self.cells)
        if len(self.buffer) > 1 << 20:
            self._flush()

    def _flush(self):
        if self.buffer:
            self.body.write(bytes(self.buffer))
            self.buffer.clear()

    def _start_cell(self, attrib):
        reference = attrib.get("r")
        if reference:
            self.col = _column_index(reference.rstrip("0123456789"))
        else:
            self.col += 1
        if self.col >= _MAX_COLS:
            raise Unsupported("column out of range")
        self.cell_attrs = attrib
        self.value = self.formula = None
        self.inline = []

    def _start_value(self, attrib):
        self.capture = self.value = []

    def _start_formula(self, attrib):
        if attrib:
            raise Unsupported("shared or array formula")
        self.capture = self.formula = []

    def _start_text(self, attrib):
        self.capture = self.inline

    def _end_capture(self):
        self.capture = None

    def _end_cell(self):
        attrs = self.cell_attrs
        col = self.col
        style_index = int(attrs.get("s", 0))
        if style_index >= self.style_count:
            raise Unsupported("unknown cell format")
        style = self.styles[style_index]
        kind = attrs.get("t", "n")
        value_text = "".join(self.value) if self.value is not None else None
        head = struct.pack("<I", col) + style
        stored = None

        if self.formula is not None:
            key = "".join(self.formula)
            if not key:
                raise Unsupported("empty formula")
            entry = self.formulas.get(key)
            if entry is None:
                tree = _parse_formula(key)
                entry = self.formulas[key] = (tree, _compile(tree, "V"))
            tree, tokens = entry
            result = _evaluate(tree, self.known) if self.remembering else 0.0
            formula = struct.pack("<I", len(tokens)) + tokens + struct.pack("<I", 0)
            if isinstance(result, _Err):
                self.cells.append(_record(11, head + bytes((_ERRORS[result.name],)) + b"\x00\x00" + formula))
            else:
                self.cells.append(_record(9, head + struct.pack("<d", result) + b"\x00\x00" + formula))
            stored = result
        elif kind == "inlineStr" or kind == "s":
            if kind == "s":
                if not value_text:
                    raise Unsupported("string cell without a value")
                text = self.shared[int(value_text)]
            else:
                text = _unescape("".join(self.inline))
            if text == "":
                self.cells.append(b"\x01\x08" + head)
            else:
                self.cells.append(b"\x07\x0c" + head + struct.pack("<I", self.strings.add(text)))
                stored = text
        elif kind == "n":
            if not value_text:
                self.cells.append(b"\x01\x08" + head)
            else:
                stored = float(value_text)
                self.cells.append(_cell_number(stored, style, col))
        elif kind == "b":
            self.cells.append(b"\x04\x09" + head + (b"\x01" if value_text == "1" else b"\x00"))
            stored = 1.0 if value_text == "1" else 0.0
        elif kind == "e":
            code = _ERRORS.get(value_text)
            if code is None:
                raise Unsupported("error value")
            self.cells.append(b"\x03\x09" + head + bytes((code,)))
            stored = _Err(value_text)
        else:
            raise Unsupported(f"cell type {kind}")

        if self.first_col is None:
            self.first_col = col
        self.last_col = col
        if self.remembering and stored is not None:
            if len(self.known) >= _EVAL_CELL_LIMIT:
                self.remembering = False
                self.known.clear()
            else:
                self.known[(self.row, col)] = stored
        self.capture = None


def _shared_strings(archive: zipfile.ZipFile, name: str | None) -> list[str]:
    if name is None:
        return []
    items: list[str] = []
    with archive.open(name) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag == _MAIN + "si":
                if element.find(_MAIN + "r") is not None or element.find(_MAIN + "rPh") is not None:
                    raise Unsupported("formatted text in a cell")
                items.append(_unescape("".join(t.text or "" for t in element.iter(_MAIN + "t"))))
                element.clear()
    return items


def _target(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = base.split("/")[:-1]
    for piece in target.split("/"):
        if piece == "..":
            parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    folder, _, leaf = part.rpartition("/")
    rels = f"{folder}/_rels/{leaf}.rels" if folder else f"_rels/{leaf}.rels"
    found = {}
    for rel in ET.fromstring(archive.read(rels)):
        found[rel.get("Id")] = (rel.get("Type"), _target(part, rel.get("Target")))
    return found


def _sheet_records(sheet: _Sheet, margins: bytes) -> tuple[bytes, bytes]:
    """What goes before and after a sheet's rows."""
    frozen = sheet.frozen_rows
    view = bytearray(_WS_VIEW)
    flags = 0x0398 | (0x04 if sheet.show_grid else 0) | (0x40 if sheet.selected else 0)
    view[0:2] = struct.pack("<H", flags)
    head = [
        _record(129),
        _record(147, _WS_PROP_FLAGS + sheet.tab_color + struct.pack("<II", 0xFFFFFFFF, 0xFFFFFFFF) + struct.pack("<I", 0)),
        _record(148, struct.pack(
            "<IIII", sheet.first_row or 0, sheet.last_row, sheet.first_col or 0, sheet.last_col)),
        _record(133), _record(137, bytes(view)),
    ]
    selection = struct.pack("<IIIIIIIII", 3, 0, 0, 0, 1, 0, 0, 0, 0)
    if frozen:
        head.append(_record(151, struct.pack("<ddIIIB", 0.0, float(frozen), frozen, 0, 2, 3)))
        head.append(_record(152, selection))
        head.append(_record(152, struct.pack("<IIIIIIIII", 2, 0, 0, 0, 1, 0, 0, 0, 0)))
    else:
        head.append(_record(152, selection))
    head += [_record(138), _record(134), _record(485, _WS_FMT_INFO)]
    if sheet.columns:
        head += [_record(390), *sheet.columns, _record(391)]
    head.append(_record(145))
    tail = [_record(146)]
    if sheet.filter:
        tail += [_record(161, struct.pack("<IIII", *sheet.filter)), _record(162)]
    tail += [_record(476, margins), _record(130)]
    return b"".join(head), b"".join(tail)


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="bin" ContentType="application/vnd.ms-excel.sheet.binary.macroEnabled.main"/>'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    "{sheets}"
    "{theme}"
    '<Override PartName="/xl/styles.bin" ContentType="application/vnd.ms-excel.styles"/>'
    '<Override PartName="/xl/sharedStrings.bin" ContentType="application/vnd.ms-excel.sharedStrings"/>'
    "</Types>"
)


def _convert(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
        root_rels = ET.fromstring(archive.read("_rels/.rels"))
        book_part = next(
            _target("workbook-root", rel.get("Target")) for rel in root_rels
            if rel.get("Type", "").endswith("/officeDocument")
        )
        book = ET.fromstring(archive.read(book_part))
        rels = _relationships(archive, book_part)

        properties = book.find(_MAIN + "workbookPr")
        date_1904 = properties is not None and properties.get("date1904") in ("1", "true")
        sheets: list[tuple[_Sheet, str, int]] = []
        for item in book.find(_MAIN + "sheets"):
            kind, part = rels[item.get(_REL + "id")]
            if not kind.endswith("/worksheet"):
                raise Unsupported("chart sheet")
            sheets.append((_Sheet(item.get("name"), item.get("state", "visible")), part, int(item.get("sheetId"))))
        if not sheets:
            raise Unsupported("no sheets")
        for defined in book.findall(f"{_MAIN}definedNames/{_MAIN}definedName"):
            if defined.get("name") != "_xlnm._FilterDatabase":
                raise Unsupported("defined name")
        views = book.find(f"{_MAIN}bookViews/{_MAIN}workbookView")
        active = int(views.get("activeTab", 0)) if views is not None else 0
        if not 0 <= active < len(sheets):
            active = 0
        sheets[active][0].selected = True

        styles_part = next((p for k, p in rels.values() if k.endswith("/styles")), None)
        shared_part = next((p for k, p in rels.values() if k.endswith("/sharedStrings")), None)
        theme_part = next((p for k, p in rels.values() if k.endswith("/theme")), None)
        if styles_part is None:
            raise Unsupported("no styles")
        styles_bin, style_count = _styles(archive.read(styles_part))
        shared = _shared_strings(archive, shared_part)
        strings = _Strings()

        count = len(sheets)
        sheet_files = [f"xl/worksheets/sheet{i}.bin" for i in range(1, count + 1)]
        has_theme = theme_part in names
        content_types = _CONTENT_TYPES.format(
            sheets="".join(
                f'<Override PartName="/{path}" ContentType="application/vnd.ms-excel.worksheet"/>'
                for path in sheet_files
            ),
            theme=(
                '<Override PartName="/xl/theme/theme1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            ) if has_theme else "",
        )

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
            out.writestr("[Content_Types].xml", content_types)
            out.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="{_TYPE}officeDocument" Target="xl/workbook.bin"/>'
                "</Relationships>",
            )
            relationships = "".join(
                f'<Relationship Id="rId{i}" Type="{_TYPE}worksheet" Target="worksheets/sheet{i}.bin"/>'
                for i in range(1, count + 1)
            )
            relationships += f'<Relationship Id="rId{count + 1}" Type="{_TYPE}styles" Target="styles.bin"/>'
            relationships += f'<Relationship Id="rId{count + 2}" Type="{_TYPE}sharedStrings" Target="sharedStrings.bin"/>'
            if has_theme:
                relationships += f'<Relationship Id="rId{count + 3}" Type="{_TYPE}theme" Target="theme/theme1.xml"/>'
                out.writestr("xl/theme/theme1.xml", archive.read(theme_part))
            out.writestr(
                "xl/_rels/workbook.bin.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f"{relationships}</Relationships>",
            )
            out.writestr("xl/styles.bin", styles_bin)

            for (sheet, part, _), path in zip(sheets, sheet_files):
                with tempfile.TemporaryFile(dir=str(destination.parent)) as body:
                    reader = _SheetReader(sheet, strings, shared, style_count, body)
                    parser = ET.XMLParser(target=reader)
                    with archive.open(part) as stream:
                        while True:
                            block = stream.read(1 << 20)
                            if not block:
                                break
                            parser.feed(block)
                    parser.close()
                    head, tail = _sheet_records(sheet, reader.margins)
                    with out.open(path, "w", force_zip64=True) as target:
                        target.write(head)
                        body.seek(0)
                        shutil.copyfileobj(body, target, 1 << 20)
                        target.write(tail)

            out.writestr("xl/sharedStrings.bin", strings.records())
            out.writestr("xl/workbook.bin", _workbook(sheets, active, date_1904))


def _workbook(sheets: list[tuple[_Sheet, str, int]], active: int, date_1904: bool) -> bytes:
    parts = [
        _record(131),
        _record(128, bytes(16) + _wide("xl") + _wide("5") + _wide("5") + _wide("9303")),
        _record(153, struct.pack("<III", 0x00010020 | (1 if date_1904 else 0), 0, 0)),
        _record(135),
        _record(158, struct.pack("<iiiiiIIB", 0x1FE, 0x267, 0x36E7, 0x14CD, 600, 0, active, 0x78)),
        _record(136),
        _record(143),
    ]
    for number, (sheet, _, sheet_id) in enumerate(sheets, 1):
        state = {"visible": 0, "hidden": 1, "veryHidden": 2}.get(sheet.state, 0)
        parts.append(_record(156, struct.pack("<II", state, sheet_id) + _wide(f"rId{number}") + _wide(sheet.name)))
    parts += [_record(144), _record(353), _record(357)]
    parts.append(_record(362, struct.pack("<I", len(sheets)) + b"".join(
        struct.pack("<III", 0, i, i) for i in range(len(sheets)))))
    parts.append(_record(354))
    for index, (sheet, _, _) in enumerate(sheets):
        if sheet.filter:
            row1, row2, col1, col2 = sheet.filter
            area = b"\x3b" + struct.pack("<HIIHH", index, row1, row2, col1, col2)
            parts.append(_record(39, struct.pack("<IBI", 0x21, 0, index) + _wide("_FilterDatabase")
                                 + struct.pack("<I", len(area)) + area + struct.pack("<I", 0) + struct.pack("<I", 0xFFFFFFFF)))
    parts.append(_record(157, struct.pack("<IIIdIH", 0x238D5, 1, 100, 0.001, 1, 0x6B)))
    parts += [_record(155, b"\x00"), _record(132)]
    return b"".join(parts)


def convert(source: str | Path, destination: str | Path) -> None:
    """Write ``source`` (.xlsx) as ``destination`` (.xlsb). Raises
    ``Unsupported`` for a workbook with something this does not convert, and
    never leaves a partly written ``destination``."""
    source, destination = Path(source), Path(destination)
    partial = destination.with_name(destination.name + ".partial")
    try:
        _convert(source, partial)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
