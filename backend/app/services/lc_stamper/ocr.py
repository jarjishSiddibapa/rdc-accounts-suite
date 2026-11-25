"""Reading a page that has no usable text: when it is needed, the engines, and matching the invoice number in OCR text.

The order of preference, cheapest first (see ``analysis``): the PDF's own text layer (no OCR at all), then the online
OCR service (free tier; only when a key is configured and the service answers), then the offline engine
(RapidOCR on the models shipped with the application - free, unlimited, needs no network).

Import-light: every worker process imports this module, so PyMuPDF, PIL, numpy and onnxruntime are loaded inside the
functions that need them.
"""

from __future__ import annotations

import base64
import functools
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

OCR_DPI_OFFLINE = 150
OCR_DPI_ONLINE = 130
HEADER_FRACTION = 0.46             # the invoice number and date are in the top part of an invoice
_MODEL_DIR = Path(__file__).resolve().parent / "models"


@dataclass(frozen=True)
class OcrConfig:
    """What a worker needs to read a page by OCR. Built by the parent process (workers do not read the .env)."""
    online_url: str = ""
    online_key: str = ""
    online_timeout: float = 25.0
    online_engine: str = "2"
    model_dir: str = ""

    @property
    def online_ready(self) -> bool:
        return bool(self.online_url and self.online_key)


# ── does this page need OCR? ───────────────────────────────────────────────────────────────────────────────────────

_BAD_CHARS = re.compile("[�-\x00-\x08\x0b\x0c\x0e-\x1f]")
_PLAUSIBLE = re.compile(r"[A-Za-z0-9\s.,:;\-/()&%@#'\"+*=_\[\]<>!?$₹ –—‘’“”|\\]")
_ASCII_LETTER = re.compile("[A-Za-z]")
_VOWEL = re.compile("[AEIOUaeiou]")

RICH_TEXT_CHARS = 600             # non-space characters: at/above this the text layer is trusted
SPARSE_IMAGE_COVERAGE = 0.30      # sparse text on a mostly-picture page is a scan
LOW_TEXT_FULL_PAGE_CHARS = 1500
LOW_TEXT_FULL_PAGE_COVERAGE = 0.85
VECTOR_CONTENT_BYTES = 6000       # no text, no picture, but this much drawing: outlined text


def text_layer_garbled(text: str) -> bool:
    """True when the text layer looks like decoding garbage (a broken font map, private-use glyphs, "(cid:12)")."""
    s = text[:6000]
    n = len(s)
    if n < 40:
        return False
    bad = len(_BAD_CHARS.findall(s)) + 5 * s.count("(cid:")
    if bad / n > 0.02:
        return True
    if len(_PLAUSIBLE.findall(s)) / n < 0.80:
        return True
    letters = len(_ASCII_LETTER.findall(s))
    if letters >= 150:
        ratio = len(_VOWEL.findall(s)) / letters
        if ratio < 0.12 or ratio > 0.62:
            return True
    return False


def _image_coverage(page) -> float:
    try:
        if not page.get_images(full=False):
            return 0.0
        rect = page.rect
        area = max(rect.width * rect.height, 1.0)
        total = 0.0
        for info in page.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            w = min(x1, rect.x1) - max(x0, rect.x0)
            h = min(y1, rect.y1) - max(y0, rect.y0)
            if w > 0 and h > 0:
                total += w * h
        return min(1.0, total / area)
    except Exception:
        return 0.0


def _has_vector_content(page) -> bool:
    try:
        return len(page.read_contents()) >= VECTOR_CONTENT_BYTES
    except Exception:
        return False


def page_needs_ocr(page_text: str, page, mode: str) -> bool:
    """off: never. always: always. auto: only when the text layer cannot be trusted (a scan, garbled text, outlined
    text). A page with a rich, sane text layer - the usual case - is never OCR-ed."""
    if mode == "off":
        return False
    if mode == "always":
        return True
    text = page_text or ""
    n = len("".join(text.split()))
    if n >= RICH_TEXT_CHARS:
        if text_layer_garbled(text):
            return True
        if n < LOW_TEXT_FULL_PAGE_CHARS:
            return _image_coverage(page) >= LOW_TEXT_FULL_PAGE_COVERAGE
        return False
    if n and text_layer_garbled(text):
        return True
    coverage = _image_coverage(page)
    if coverage >= SPARSE_IMAGE_COVERAGE:
        return True
    if n < 20 and coverage == 0.0:
        return _has_vector_content(page)
    return False


# ── rendering ──────────────────────────────────────────────────────────────────────────────────────────────────────

def render_gray(page, dpi: int, top_fraction: Optional[float] = None):
    """The page (or its top part) as a grayscale PIL image."""
    import pymupdf as fitz
    from PIL import Image
    clip = None
    if top_fraction:
        rect = page.rect
        clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * top_fraction)
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False, clip=clip)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


# ── online OCR (a free OCR.space style service) ────────────────────────────────────────────────────────────────────

class OnlineUnavailable(Exception):
    """The online service cannot be used now (no key, no network, quota used up). The caller goes offline."""


_online_lock = threading.Lock()
_online_state = {"fails": 0, "off_until": 0.0}
_QUOTA_WORDS = ("limit", "quota", "exceed", "too many", "maximum", "rate", "forbidden", "invalid api", "key")


def online_usable(cfg: OcrConfig) -> bool:
    return cfg.online_ready and time.monotonic() >= _online_state["off_until"]


def _online_failed(seconds: float, hard: bool = False) -> None:
    with _online_lock:
        _online_state["fails"] += 1
        if hard or _online_state["fails"] >= 2:         # quota / key problems at once, other trouble the second time
            _online_state["off_until"] = time.monotonic() + seconds
            _online_state["fails"] = 0


def _multipart(fields: dict, file_name: str, file_bytes: bytes) -> tuple:
    boundary = uuid.uuid4().hex
    lines = []
    for name, value in fields.items():
        lines.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    lines.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
         "Content-Type: image/jpeg\r\n\r\n").encode() + file_bytes + b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())
    return b"".join(lines), f"multipart/form-data; boundary={boundary}"


def read_online(img, cfg: OcrConfig) -> str:
    """OCR the page image with the online service. Raises OnlineUnavailable when it cannot be used."""
    if not online_usable(cfg):
        raise OnlineUnavailable("online OCR is not available")
    buffer = io.BytesIO()
    quality = 72
    img.save(buffer, "JPEG", quality=quality)
    while buffer.tell() > 950_000 and quality > 30:       # the free tier takes images up to 1 MB
        quality -= 14
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=quality)
    body, content_type = _multipart(
        {"apikey": cfg.online_key, "language": "eng", "OCREngine": cfg.online_engine, "scale": "true",
         "isTable": "false", "isOverlayRequired": "false"},
        "page.jpg", buffer.getvalue())
    request = urllib.request.Request(cfg.online_url, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=cfg.online_timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        _online_failed(1800 if exc.code in (401, 403, 429) else 120, hard=exc.code in (401, 403, 429))
        raise OnlineUnavailable(f"online OCR answered {exc.code}") from exc
    except Exception as exc:                                # no network, DNS, timeout, bad JSON
        _online_failed(300)
        raise OnlineUnavailable(f"online OCR not reachable ({exc.__class__.__name__})") from exc
    if payload.get("IsErroredOnProcessing") or not payload.get("ParsedResults"):
        message = " ".join(payload.get("ErrorMessage") or []) if isinstance(payload.get("ErrorMessage"), list) \
            else str(payload.get("ErrorMessage") or "")
        _online_failed(1800 if any(w in message.lower() for w in _QUOTA_WORDS) else 120,
                       hard=any(w in message.lower() for w in _QUOTA_WORDS))
        raise OnlineUnavailable(message or "online OCR could not read the page")
    with _online_lock:
        _online_state["fails"] = 0
    return "\n".join(str(item.get("ParsedText") or "") for item in payload["ParsedResults"])


# ── offline OCR (RapidOCR on the shipped models) ───────────────────────────────────────────────────────────────────

_ENGINE_LOCK = threading.Lock()
_ENGINE = {}


def offline_available(cfg: Optional[OcrConfig] = None) -> bool:
    model_dir = Path((cfg.model_dir if cfg and cfg.model_dir else "") or _MODEL_DIR)
    if not (model_dir / "PP-OCRv6_det_small.onnx").is_file() or not (model_dir / "PP-OCRv6_rec_small.onnx").is_file():
        return False
    try:
        import importlib.util
        return importlib.util.find_spec("rapidocr") is not None and importlib.util.find_spec("onnxruntime") is not None
    except Exception:
        return False


def _offline_engine(cfg: OcrConfig):
    with _ENGINE_LOCK:
        engine = _ENGINE.get("rapid")
        if engine is None:
            os.environ.setdefault("OMP_NUM_THREADS", "1")
            from rapidocr import RapidOCR
            model_dir = Path(cfg.model_dir or _MODEL_DIR)
            engine = RapidOCR(params={
                "Det.model_path": str(model_dir / "PP-OCRv6_det_small.onnx"),
                "Rec.model_path": str(model_dir / "PP-OCRv6_rec_small.onnx"),
                "Cls.model_path": str(model_dir / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
                "Global.use_cls": False,
                "Global.log_level": "error",
                "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            })
            _ENGINE["rapid"] = engine
        return engine


def _lines_from(boxes, texts) -> str:
    """Boxes of recognised text -> the page's lines (boxes on the same row are joined left to right)."""
    items = []
    for box, text in zip(boxes, texts):
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append((sum(ys) / len(ys), min(xs), max(ys) - min(ys), str(text)))
    if not items:
        return ""
    items.sort(key=lambda item: (item[0], item[1]))
    heights = sorted(item[2] for item in items)
    tolerance = max(4.0, heights[len(heights) // 2] * 0.6)
    lines, current, anchor = [], [], None
    for y, x, _, text in items:
        if anchor is None or abs(y - anchor) <= tolerance:
            current.append((x, text))
            anchor = y if anchor is None else (anchor + y) / 2
        else:
            lines.append(" ".join(t for _, t in sorted(current)))
            current, anchor = [(x, text)], y
    if current:
        lines.append(" ".join(t for _, t in sorted(current)))
    return "\n".join(lines)


def read_offline(img, cfg: OcrConfig) -> str:
    import numpy as np
    result = _offline_engine(cfg)(np.asarray(img))
    if result is None or getattr(result, "boxes", None) is None or getattr(result, "txts", None) is None:
        return ""
    return _lines_from(result.boxes, result.txts)


def read_page(page, cfg: OcrConfig, online: bool, top_fraction: Optional[float] = None) -> tuple:
    """OCR one page (or its top part). Online first when allowed and usable, then offline.
    Returns (text, "online" | "offline"). Raises RuntimeError when no engine can read it."""
    if online and online_usable(cfg):
        try:
            return read_online(render_gray(page, OCR_DPI_ONLINE, top_fraction), cfg), "online"
        except OnlineUnavailable:
            pass
    if offline_available(cfg):
        return read_offline(render_gray(page, OCR_DPI_OFFLINE, top_fraction), cfg), "offline"
    raise RuntimeError("This page needs OCR, but no OCR engine is available (online OCR is not set up and the "
                       "offline models are missing).")


# ── matching the invoice number ────────────────────────────────────────────────────────────────────────────────────

_MIN_FUZZY_LEN = 6
_CONFUSION = {}
for _cls, _chars in (("0", "Oo"), ("1", "Il|!"), ("5", "Ss"), ("8", "Bb"), ("2", "Zz"), ("6", "Gg")):
    for _c in _chars:
        _CONFUSION[_c] = _cls
_NON_ALNUM_KEEP = "|!"
_RUN_RE = re.compile(r"[A-Za-z0-9|!]+")
_MAX_JOIN_RUNS = 4


def _canon(s: str) -> str:
    return "".join(_CONFUSION.get(ch) or ch.upper() for ch in s if ch.isalnum() or ch in _NON_ALNUM_KEEP)


def _alnum(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in _NON_ALNUM_KEEP)


def _max_subs(n: int) -> int:
    return 1 if n < 10 else (2 if n < 16 else 3)


def _edge_ok(text: str, start: int, end: int) -> bool:
    """A match must not be the inside of a longer number/word: "1001" is not a match inside "10012"."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    first, last = text[start], text[end - 1]
    if before and ((before.isdigit() and first.isdigit()) or (before.isalpha() and first.isalpha())):
        return False
    if after and ((after.isdigit() and last.isdigit()) or (after.isalpha() and last.isalpha())):
        return False
    return True


@functools.lru_cache(maxsize=8)
def _exact_pattern(keys: tuple):
    ordered = sorted({k for k in keys if k}, key=len, reverse=True)
    return re.compile("|".join(map(re.escape, ordered))) if ordered else None


def match_exact(keys: Sequence[str], text: str) -> Optional[str]:
    """The longest invoice number of ``keys`` that is written on the page (whole numbers only)."""
    if not keys or not text:
        return None
    pattern = _exact_pattern(tuple(keys))
    best = None
    for match in pattern.finditer(text):
        if _edge_ok(text, match.start(), match.end()) and (best is None or len(match.group()) > len(best)):
            best = match.group()
    return best


class KeyMatcher:
    """OCR-confusion tolerant matching (O/0, I/l/1, S/5, B/8, Z/2, G/6, stray spaces and dashes) for numbers of at
    least 6 characters. It fires only when exactly one number of the list fits, so INV-1001 and INV-1007 stay apart."""

    def __init__(self, keys: Sequence[str]):
        canon_map: dict = {}
        for key in dict.fromkeys(k for k in keys if k):
            if len(_alnum(key)) >= _MIN_FUZZY_LEN:
                canon_map.setdefault(_canon(key), []).append(key)
        self.canon_map = {c: ks[0] for c, ks in canon_map.items() if len(ks) == 1}
        self.fuzzy_lens = {len(c) for c in self.canon_map}
        self.max_len = max(self.fuzzy_lens) if self.fuzzy_lens else 0

    def fuzzy(self, text: str) -> Optional[str]:
        if not self.canon_map:
            return None
        hits = set()
        for line in text.splitlines():
            runs = [(m.start(), m.end(), m.group()) for m in _RUN_RE.finditer(line)]
            for i in range(len(runs)):
                raw = canon = ""
                for j in range(i, min(i + _MAX_JOIN_RUNS, len(runs))):
                    if j > i and runs[j][0] - runs[j - 1][1] > 2:
                        break
                    raw += runs[j][2]
                    canon += _canon(runs[j][2])
                    if len(canon) > self.max_len:
                        break
                    if len(canon) in self.fuzzy_lens:
                        key = self.canon_map.get(canon)
                        if key is not None:
                            ka, ca = _alnum(key), _alnum(raw)
                            if sum(1 for a, b in zip(ka, ca) if a != b) <= _max_subs(len(ka)):
                                hits.add(key)
        return next(iter(hits)) if len(hits) == 1 else None


@functools.lru_cache(maxsize=8)
def _matcher_for(keys: tuple) -> KeyMatcher:
    return KeyMatcher(keys)


def match_ocr_text(keys: Sequence[str], text: str) -> Optional[str]:
    """The invoice number found in OCR text: exact (whole number) first, then the confusion-tolerant match."""
    if not keys or not text:
        return None
    keys = keys if isinstance(keys, tuple) else tuple(keys)
    return match_exact(keys, text) or _matcher_for(keys).fuzzy(text)
