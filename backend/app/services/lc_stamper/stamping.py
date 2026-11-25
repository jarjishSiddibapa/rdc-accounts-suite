"""WYSIWYG stamp drawing + preview rendering (rotation / cropbox aware).

Design (see docs/notes/stamp.md):
* ONE drawing function (`_draw_lines`) is used both for the final PDF stamp
  (`stamp_page`) and for the preview sprite (`render_stamp_sprite`), so glyph
  shapes, sizes, line spacing and sub-pixel positions are identical.
* Position = top-left of the text box on the VISUAL page (after /Rotate, relative
  to the CropBox), as a fraction of the visual page size. The box is
  (max line width) x (n_lines * font_size * (ascender - descender)) pt; the first
  baseline sits at box_top + font_size * ascender.
* Fonts are loaded once per process (module-level cache). Latin-1 text uses the
  PDF base-14 Helvetica / Helvetica-Bold (not embedded). Anything else (e.g. the
  rupee sign, euro, curly quotes) switches the WHOLE stamp to an embedded TrueType
  fallback (bundled `lc_stamper/fonts/*.ttf` if present, else Arial / Segoe UI /
  DejaVu from the OS) so no glyph degrades to '?'. Glyphs missing even in the
  fallback become '?'. If no fallback font exists, non-Latin-1 chars become '?'.
* Nothing is moved or clamped: if the box overflows the page, the (overflowing)
  rect is still returned and drawn; the UI decides what to do.
"""
from __future__ import annotations

import math
import os
import sys
import threading
from dataclasses import dataclass
from typing import Optional

import pymupdf as fitz

from .models import StampSpec

__all__ = [
    "PageGeometry", "RasterImage", "Sprite", "page_geometry", "text_box_size",
    "box_rect", "stamp_page", "render_page_preview", "render_stamp_sprite",
]

SPRITE_MARGIN_PX = 2          # transparent safety margin around the sprite (glyph overhang)
_MAX_SPRITE_PX = 16000
# MuPDF snaps glyph origins to a 1/4 px grid. "Nice" positions (0.5*W, integer pt ...) sit exactly ON a
# grid edge, where float32 noise (different in rotated/cropped page matrices) flips the cell. Both the
# stamp and the sprite draw at (x+EPS, y+EPS) so such ties resolve identically. 0.002 pt << 0.5 pt tol.
_TIE_EPS_PT = 0.002


@dataclass
class PageGeometry:
    width: float      # VISUAL width in pt (after /Rotate, CropBox based)
    height: float     # VISUAL height in pt
    rotation: int     # /Rotate of the page (0/90/180/270)


@dataclass
class RasterImage:
    data: bytes
    width: int
    height: int
    stride: int
    fmt: str          # "RGB888" | "RGBA8888"  (RGBA8888 = STRAIGHT / non-premultiplied alpha)


@dataclass
class Sprite(RasterImage):
    """RasterImage plus where to draw it. (x, y) = integer pixel position of the
    sprite's top-left corner on a preview rendered at the same zoom (may be
    slightly negative when the anchor is near the page edge)."""
    x: int = 0
    y: int = 0


# --------------------------------------------------------------------------- fonts
@dataclass(frozen=True)
class _FontInfo:
    pdf_name: str                 # name used by insert_text (fontname=)
    buffer: Optional[bytes]       # TTF bytes for embedded fonts, None for base-14
    font: fitz.Font               # metrics object
    asc: float
    desc: float                   # negative

    @property
    def line_height(self) -> float:   # per pt of font size
        return self.asc - self.desc


_lock = threading.Lock()
_font_cache: dict = {}
_FALLBACK_NAMES = {
    False: ("RDCStamp-Regular.ttf", "NotoSans-Regular.ttf", "arial.ttf", "segoeui.ttf",
            "DejaVuSans.ttf", "Arial.ttf"),
    True: ("RDCStamp-Bold.ttf", "NotoSans-Bold.ttf", "arialbd.ttf", "segoeuib.ttf",
           "DejaVuSans-Bold.ttf", "Arial Bold.ttf"),
}


def _font_dirs() -> list:
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [os.path.join(here, "fonts")]
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(getattr(sys, "_MEIPASS", ""), "fonts"))
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        dirs.append(os.path.join(windir, "Fonts"))
    dirs += ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF",
             "/Library/Fonts", "/System/Library/Fonts/Supplemental"]
    return dirs


def _base14(bold: bool) -> _FontInfo:
    key = ("b14", bold)
    fi = _font_cache.get(key)
    if fi is None:
        with _lock:
            name = "hebo" if bold else "helv"
            f = fitz.Font(name)
            fi = _FontInfo(name, None, f, f.ascender, f.descender)
            _font_cache[key] = fi
    return fi


def _fallback(bold: bool) -> Optional[_FontInfo]:
    key = ("uni", bold)
    if key in _font_cache:
        return _font_cache[key]
    with _lock:
        if key in _font_cache:
            return _font_cache[key]
        fi = None
        for d in _font_dirs():
            for n in _FALLBACK_NAMES[bold]:
                p = os.path.join(d, n)
                if not os.path.isfile(p):
                    continue
                try:
                    with open(p, "rb") as fh:
                        buf = fh.read()
                    f = fitz.Font(fontbuffer=buf)
                    if not f.has_glyph(0x20B9):          # must cover the rupee sign
                        continue
                    stem = "".join(ch for ch in os.path.splitext(n)[0] if ch.isalnum())
                    fi = _FontInfo("RDC" + stem, buf, f, f.ascender, f.descender)
                    break
                except Exception:
                    continue
            if fi:
                break
        _font_cache[key] = fi
        return fi


def _latin1_safe(ch: str) -> bool:
    o = ord(ch)
    return 32 <= o <= 126 or 160 <= o <= 255


def _prepare(spec: StampSpec):
    """-> (lines, _FontInfo). Normalises text and picks the font for the WHOLE stamp."""
    text = (spec.text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = "".join(ch for ch in text if ch == "\n" or ch >= " " and not (0x7F <= ord(ch) <= 0x9F))
    lines = text.split("\n")
    if not any(lines):
        return [], _base14(bool(spec.bold))
    bold = bool(spec.bold)
    if all(_latin1_safe(ch) for ln in lines for ch in ln):
        return lines, _base14(bold)
    fb = _fallback(bold)
    if fb is None:                                       # no Unicode font anywhere
        return [("".join(ch if _latin1_safe(ch) else "?" for ch in ln)) for ln in lines], _base14(bold)
    lines = ["".join(ch if fb.font.has_glyph(ord(ch)) else "?" for ch in ln) for ln in lines]
    return lines, fb


def _fs(spec: StampSpec) -> float:
    try:
        return float(spec.font_size)
    except (TypeError, ValueError):
        return 0.0


def text_box_size(spec: StampSpec) -> tuple:
    """Exact (width, height) in pt of the drawn text box; (0, 0) for empty text."""
    fs = _fs(spec)
    lines, fi = _prepare(spec)
    if not lines or fs <= 0:
        return (0.0, 0.0)
    w = max(fi.font.text_length(ln, fs) for ln in lines)
    return (float(w), float(len(lines) * fs * fi.line_height))


def box_rect(spec: StampSpec, page_w: float, page_h: float) -> fitz.Rect:
    """Visual-page rect the stamp occupies (no clamping; may overflow the page)."""
    bw, bh = text_box_size(spec)
    x, y = spec.rel_x * page_w, spec.rel_y * page_h
    return fitz.Rect(x, y, x + bw, y + bh)


def _color(spec: StampSpec) -> tuple:
    c = tuple(spec.color or (0, 0, 0))[:3]
    c = tuple(min(1.0, max(0.0, float(v))) for v in c)
    return c if len(c) == 3 else (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- drawing
def _draw_lines(page: fitz.Page, x: float, y: float, lines, fi: _FontInfo, fs: float, color) -> None:
    """THE single drawing routine. (x, y) = box top-left in VISUAL page coordinates.
    Draws real vector text, upright for the viewer, on top of existing content."""
    rot = page.rotation
    deromat = page.derotation_matrix
    lh = fs * fi.line_height
    x, y = x + _TIE_EPS_PT, y + _TIE_EPS_PT
    if fi.buffer is not None:       # embedded font: registered once per document (same xref reused)
        page.insert_font(fontname=fi.pdf_name, fontbuffer=fi.buffer)
    shape = page.new_shape()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        base = fitz.Point(x, y + i * lh + fs * fi.asc) * deromat     # baseline-left, unrotated space
        shape.insert_text(base, ln, fontsize=fs, fontname=fi.pdf_name, color=color,
                          rotate=rot)
    shape.commit(overlay=True)


def page_geometry(page: fitz.Page) -> PageGeometry:
    r = page.rect
    return PageGeometry(float(r.width), float(r.height), int(page.rotation))


def stamp_page(page: fitz.Page, spec: StampSpec) -> fitz.Rect:
    """Stamp `page` (vector text, overlay). Returns the box rect in VISUAL page
    coordinates (top-left = (rel_x*W, rel_y*H)). Empty text/size -> nothing drawn."""
    g = page.rect
    rect = box_rect(spec, g.width, g.height)
    fs = _fs(spec)
    lines, fi = _prepare(spec)
    if not lines or fs <= 0:
        return rect
    _draw_lines(page, rect.x0, rect.y0, lines, fi, fs, _color(spec))
    return rect


# --------------------------------------------------------------------------- rendering
def render_page_preview(pdf_path: str, page_index: int, zoom: float,
                        password: str = "") -> tuple:
    """Render one page (visual: /Rotate + CropBox applied, annotations on) as RGB.
    Returns (RasterImage(fmt="RGB888"), PageGeometry). Pixel (0,0) = visual page
    top-left; scale is exactly `zoom` px per pt."""
    doc = fitz.open(pdf_path)
    try:
        if doc.needs_pass and not doc.authenticate(password or ""):
            raise ValueError("PDF is password protected")
        page = doc.load_page(page_index)
        geo = page_geometry(page)
        pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
        img = RasterImage(bytes(pm.samples), pm.width, pm.height, pm.stride, "RGB888")
        return img, geo
    finally:
        doc.close()


def render_stamp_sprite(spec: StampSpec, zoom: float,
                        anchor_px: tuple = (0.0, 0.0)) -> Sprite:
    """Rasterise the stamp with PyMuPDF (same `_draw_lines` as the final stamp).

    Returns RGBA, 8 bit, STRAIGHT (non-premultiplied) alpha -> wrap in
    QImage.Format_RGBA8888 (NOT _Premultiplied). `anchor_px` = (rel_x*W*zoom,
    rel_y*H*zoom): the exact (fractional) pixel where the box top-left must land on
    the preview. The sprite is rendered with that sub-pixel phase and its
    `.x/.y` give the integer pixel at which to draw it (preview px = sprite px + x/y).
    """
    bw, bh = text_box_size(spec)
    ax, ay = float(anchor_px[0]), float(anchor_px[1])
    ix, iy = math.floor(ax), math.floor(ay)
    fx, fy = ax - ix, ay - iy
    m = SPRITE_MARGIN_PX
    lines, fi = _prepare(spec)
    fs = _fs(spec)
    if not lines or fs <= 0 or bw <= 0 or bh <= 0:
        return Sprite(b"\x00\x00\x00\x00", 1, 1, 4, "RGBA8888", ix, iy)
    wpx = min(_MAX_SPRITE_PX, int(math.ceil(m + fx + bw * zoom)) + m + 1)
    hpx = min(_MAX_SPRITE_PX, int(math.ceil(m + fy + bh * zoom)) + m + 1)
    doc = fitz.open()
    try:
        page = doc.new_page(width=wpx / zoom, height=hpx / zoom)
        _draw_lines(page, (m + fx) / zoom, (m + fy) / zoom, lines, fi, fs, _color(spec))
        pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=True)
        w, h = pm.width, pm.height
        data = _unpremultiply(bytes(pm.samples), w, h, pm.stride)
    finally:
        doc.close()
    return Sprite(data, w, h, w * 4, "RGBA8888", ix - m, iy - m)


def _unpremultiply(buf: bytes, w: int, h: int, stride: int) -> bytes:
    import numpy as np
    a = np.frombuffer(buf, np.uint8).reshape(h, stride)[:, : w * 4].reshape(h, w, 4).astype(np.uint16)
    al = a[..., 3:4]
    rgb = np.where(al > 0, (a[..., :3] * 255 + al // 2) // np.maximum(al, 1), 0)
    out = np.concatenate([np.minimum(rgb, 255), al], axis=2).astype(np.uint8)
    return out.tobytes()


# --------------------------------------------------------------------------- PNG for the browser
def page_png(pdf_path: str, page_index: int, zoom: float, password: str = "") -> tuple:
    """The page as PNG bytes (what the browser shows) and its geometry."""
    img, geo = render_page_preview(pdf_path, page_index, zoom, password)
    from PIL import Image
    import io
    buffer = io.BytesIO()
    Image.frombytes("RGB", (img.width, img.height), img.data, "raw", "RGB", img.stride).save(
        buffer, "PNG", compress_level=1)
    return buffer.getvalue(), geo


def stamp_png(spec: StampSpec, zoom: float) -> tuple:
    """The stamp alone as a transparent PNG (drawn by the same code as the real stamp), the margin in pixels around
    the text box, the image size and the text box size in pt. The browser lays it on the page at (rel_x, rel_y)
    minus that margin."""
    from PIL import Image
    import io
    sprite = render_stamp_sprite(spec, zoom, (0.0, 0.0))
    image = Image.frombytes("RGBA", (sprite.width, sprite.height), sprite.data)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=1)
    return buffer.getvalue(), SPRITE_MARGIN_PX, (sprite.width, sprite.height), text_box_size(spec)
