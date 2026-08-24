"""
DMS document fetch/scrape pipeline — ported from
sneha-raman-dms-document-downloader/sneha_dms_downloader.py, with the
tkinter GUI stripped out. Framework-agnostic: fetch_document(url) does all
the work and returns a FetchResult with an in-memory blob; the FastAPI
route layer handles streaming it back to the browser.
"""

import mimetypes
import re
import urllib.parse
from pathlib import Path

import requests

from app.config import MAX_DMS_DOWNLOAD_BYTES

MIME_TO_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/png":  ".png", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp",
    "image/tiff": ".tif", "image/x-tiff": ".tif",
    "application/pdf": ".pdf",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _validate_url(url: str) -> str:
    url = url.strip()
    # Match the desktop UI: a pasted host/path without a scheme is treated as
    # HTTPS instead of being rejected.
    if url and not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only valid HTTP or HTTPS document URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not supported")
    return urllib.parse.urlunsplit(parsed)


def _read_limited(response) -> bytes:
    declared_size = response.headers.get("Content-Length")
    if (
        MAX_DMS_DOWNLOAD_BYTES > 0
        and declared_size
        and declared_size.isdigit()
        and int(declared_size) > MAX_DMS_DOWNLOAD_BYTES
    ):
        raise ValueError(
            f"Document exceeds the {MAX_DMS_DOWNLOAD_BYTES // (1024 * 1024)}MB download limit"
        )
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(65_536):
        if not chunk:
            continue
        total += len(chunk)
        if MAX_DMS_DOWNLOAD_BYTES > 0 and total > MAX_DMS_DOWNLOAD_BYTES:
            raise ValueError(
                f"Document exceeds the {MAX_DMS_DOWNLOAD_BYTES // (1024 * 1024)}MB download limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def sniff_mime(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] in (b"II", b"MM"):
        return "image/tiff"
    if data[:2] == b"BM":
        return "image/bmp"
    return ""


def is_html(data: bytes, mime: str) -> bool:
    if "html" in mime:
        return True
    head = data[:1024].lower()
    return b"<html" in head or b"<!doctype" in head


def ext_for(mime: str, url: str, cd: str) -> tuple[str, str]:
    """Returns (ext_with_dot, suggested_filename)."""
    if cd:
        m = re.search(r'filename[^;=\n]*=(["\']?)([^"\';\n]+)\1', cd, re.I)
        if m:
            fn = m.group(2).strip()
            return Path(fn).suffix, fn
    ext = MIME_TO_EXT.get(mime.lower(), "")
    if ext:
        return ext, "document" + ext
    path = urllib.parse.urlparse(url).path
    ext = Path(path).suffix
    if ext:
        return ext, Path(path).name or ("document" + ext)
    ext = mimetypes.guess_extension(mime) or ""
    if ext == ".jpe":
        ext = ".jpg"
    if ext:
        return ext, "document" + ext
    return "", "document"


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1_048_576:
        return f"{n/1024:.1f} KB"
    return f"{n/1_048_576:.1f} MB"


def extract_doc_url(html: str, base_url: str) -> str | None:
    """Try multiple strategies to find the real document URL inside a DMS
    viewer page. Returns the absolute URL of the document, or None."""

    for m in re.finditer(r'viewer[^"\']*\.html\?[^"\']*file=([^"\'&\s#]+)', html, re.I):
        file_url = urllib.parse.unquote(m.group(1))
        abs_url = urllib.parse.urljoin(base_url, file_url)
        if abs_url.startswith("http"):
            return abs_url

    for pat in [
        r'<embed[^>]+src\s*=\s*["\']([^"\']+)["\']',
        r'<object[^>]+data\s*=\s*["\']([^"\']+)["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            return urllib.parse.urljoin(base_url, m.group(1))

    for m in re.finditer(r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']', html, re.I):
        src = m.group(1)
        if any(src.lower().endswith(e) for e in
               ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.pdf')):
            return urllib.parse.urljoin(base_url, src)

    for m in re.finditer(r'<iframe[^>]+src\s*=\s*["\']([^"\']+)["\']', html, re.I):
        src = m.group(1).strip()
        if not src or src.startswith('#'):
            continue
        fp = re.search(r'[?&]file=([^&"\'#\s]+)', src, re.I)
        if fp:
            file_url = urllib.parse.unquote(fp.group(1))
            abs_url = urllib.parse.urljoin(base_url, file_url)
            if abs_url.startswith("http"):
                return abs_url
        return urllib.parse.urljoin(base_url, src)

    for pat in [
        r'["\']([^"\']*(?:download|file|document|doc|attachment)[^"\']*\.[a-z]{2,5})["\']',
        r'url\s*[:=]\s*["\']([^"\']+\.[a-z]{2,5})["\']',
        r'src\s*[:=]\s*["\']([^"\']+\.[a-z]{2,5})["\']',
        r'href\s*[:=]\s*["\']([^"\']+\.[a-z]{2,5})["\']',
    ]:
        for m in re.finditer(pat, html, re.I):
            cand = m.group(1)
            if any(cand.lower().endswith(e) for e in
                   ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
                    '.tif', '.tiff', '.pdf', '.docx', '.xlsx', '.zip')):
                abs_url = urllib.parse.urljoin(base_url, cand)
                if abs_url.startswith("http"):
                    return abs_url

    for pat in [
        r'["\']([^"\']*(?:GetFile|DownloadFile|ViewFile|GetDocument|'
        r'download|stream|render|file)[^"\']*)["\']',
    ]:
        for m in re.finditer(pat, html, re.I):
            cand = m.group(1)
            if ' ' in cand and '/' not in cand and not cand.startswith('http'):
                continue
            abs_url = urllib.parse.urljoin(base_url, cand)
            if abs_url.startswith("http") and len(cand) > 5:
                return abs_url

    for m in re.finditer(
        r'(https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|gif|pdf|webp|bmp|tiff?))',
        html, re.I
    ):
        return m.group(1)

    return None


class FetchResult:
    def __init__(self):
        self.blob: bytes = b""
        self.mime: str = ""
        self.ext: str = ""
        self.fname: str = "document"
        self.final_url: str = ""
        self.steps: list[str] = []
        self.error: str = ""
        self.error_detail: str = ""


def fetch_document(url: str) -> FetchResult:
    """Full fetch pipeline: GET -> if HTML, scrape real doc URL -> GET that
    -> return blob + metadata. Identical logic to the desktop tool."""
    res = FetchResult()
    try:
        url = _validate_url(url)
    except ValueError as exc:
        res.error = "INVALID_URL"
        res.error_detail = str(exc)
        return res
    session = requests.Session()

    def get(u: str, stream=True):
        return session.get(u, headers=BROWSER_HEADERS,
                            timeout=30, allow_redirects=True, stream=stream)

    try:
        res.steps.append(f"GET {url}")
        r = get(url)
        r.raise_for_status()

        mime1 = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        cd1 = r.headers.get("Content-Disposition", "")

        data1 = _read_limited(r)
        sniffed1 = sniff_mime(data1)

        res.steps.append(
            f"  -> {r.status_code}  Content-Type: {mime1 or '(none)'}  "
            f"Size: {fmt_size(len(data1))}  Sniffed: {sniffed1 or '(html/unknown)'}"
        )

        effective_mime = sniffed1 or mime1

        if is_html(data1, mime1) or (not sniffed1 and not mime1):
            res.steps.append("  -> Response is HTML - scraping for real document URL...")
            html_text = data1.decode("utf-8", errors="replace")
            doc_url = extract_doc_url(html_text, r.url)

            if doc_url:
                res.steps.append(f"  -> Found document URL: {doc_url}")

                doc_url = _validate_url(doc_url)
                r2 = get(doc_url)
                r2.raise_for_status()
                mime2 = (r2.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                cd2 = r2.headers.get("Content-Disposition", "")

                data2 = _read_limited(r2)
                sniffed2 = sniff_mime(data2)
                effective_mime = sniffed2 or mime2

                res.steps.append(
                    f"  -> {r2.status_code}  Content-Type: {mime2}  "
                    f"Size: {fmt_size(len(data2))}  Sniffed: {sniffed2 or '(unknown)'}"
                )

                res.blob = data2
                res.final_url = doc_url
                ext, fname = ext_for(effective_mime, doc_url, cd2)
                res.ext = ext
                res.fname = fname
                res.mime = effective_mime

            else:
                res.steps.append(
                    "  X Could not locate document URL in HTML page.\n"
                    "    The page may require authentication or use JavaScript rendering.\n"
                    "    Saving the HTML page so you can inspect it."
                )
                res.blob = data1
                res.mime = "text/html"
                res.ext = ".html"
                res.fname = "viewer_page.html"
                res.final_url = r.url
                res.error = "HTML_ONLY"
                res.error_detail = (
                    "The permalink returned an HTML viewer page but the app could not "
                    "automatically find the embedded document URL.\n\n"
                    "This can happen when:\n"
                    "  - The page requires you to be logged in to the DMS\n"
                    "  - The document URL is generated dynamically by JavaScript\n\n"
                    "Try: copy the image/document URL from the DMS viewer manually "
                    "(right-click the document -> Copy image address) and paste that "
                    "URL here instead."
                )

        else:
            res.blob = data1
            res.mime = effective_mime
            res.final_url = r.url
            ext, fname = ext_for(effective_mime, r.url, cd1)
            res.ext = ext
            res.fname = fname

    except requests.exceptions.HTTPError as e:
        res.error = f"HTTP {e.response.status_code}"
        res.error_detail = str(e)
    except requests.exceptions.ConnectionError:
        res.error = "Connection failed"
        res.error_detail = "Could not reach the server. Check your network connection."
    except requests.exceptions.Timeout:
        res.error = "Request timed out"
        res.error_detail = "The server did not respond within 30 seconds."
    except Exception as e:
        res.error = "Unexpected error"
        res.error_detail = str(e)

    return res
