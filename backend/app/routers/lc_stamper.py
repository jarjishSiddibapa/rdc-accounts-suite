"""RDC LC Stamper Application routes (stable app key: lc-stamper).

Find the invoice pages of one or many PDFs by the invoice list, check their date and amount, stamp them (or only pick
them out) and report. The flow of the page:

  1. POST /documents          upload PDFs (kept per user for a day, so the rules can be changed and the run repeated)
  2. POST /list, /list/paste  read the invoice list (Excel / CSV / typed numbers); it is shown back for checking
  3. GET  /documents/{id}/pages/{n}, POST /stamp-preview
                              the page and the stamp, drawn by the code that writes the real stamp
  4. POST /run                the batch as a background job (documents read and stamped side by side)
  5. GET  /jobs/{id}, /download/{id}/...   progress and the finished PDFs, report and zip
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app import config, jobs
from app.auth import get_current_user
from app.config import SCRATCH_DIR
from app.jobs import JobCancelled, JobUserError, cancel_job, get_job, submit_job
from app.models import User
from app.permissions import require_app_access

router = APIRouter(
    prefix="/api/tools/lc-stamper", tags=["lc-stamper"],
    dependencies=[Depends(require_app_access("lc-stamper"))],
)

_ROOT = SCRATCH_DIR / "lc-stamper"
_ID = re.compile(r"^[0-9a-f]{32}$")
_CHUNK = 1024 * 1024
_MAX_DOCUMENTS = 200
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ── request bodies ─────────────────────────────────────────────────────────────────────────────────────────────────

class StampIn(BaseModel):
    text: str = Field("", max_length=600)
    font_size: float = Field(12.0, ge=4, le=120)
    color: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    rel_x: float = Field(0.05, ge=-0.5, le=1.5)
    rel_y: float = Field(0.05, ge=-0.5, le=1.5)
    bold: bool = True


class RulesIn(BaseModel):
    match_dates: bool = False
    match_amounts: bool = False
    amount_tolerance: float = Field(1.0, ge=0, le=1_000_000)
    skip_blank: bool = True
    ocr_mode: Literal["off", "auto", "always"] = "auto"
    online_ocr: bool = True
    keep_duplicates: bool = False


class RunIn(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=_MAX_DOCUMENTS)
    list_id: Optional[str] = None
    mode: Literal["stamp", "filter"] = "stamp"
    output: Literal["per_document", "combined"] = "per_document"
    rules: RulesIn = Field(default_factory=RulesIn)
    stamp: StampIn = Field(default_factory=StampIn)


class PasteIn(BaseModel):
    text: str = Field(max_length=2_000_000)


class StampPreviewIn(BaseModel):
    stamp: StampIn
    zoom: float = Field(1.5, ge=0.3, le=4)
    list_id: Optional[str] = None
    document_name: str = ""


# ── staging (a folder of the user's own) ───────────────────────────────────────────────────────────────────────────

def _user_dir(user: User) -> Path:
    path = _ROOT / str(user.id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _purge_old(user_dir: Path) -> None:
    """Uploads and outputs older than the retention are removed (the person has had a day to use them)."""
    cutoff = time.time() - config.LC_STAMPER_RETENTION_HOURS * 3600
    try:
        for item in user_dir.iterdir():
            if item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)
        for out in SCRATCH_DIR.glob("lc-out-*"):
            if out.is_dir() and out.stat().st_mtime < cutoff:
                shutil.rmtree(out, ignore_errors=True)
    except OSError:
        pass


def _check_id(value: str) -> str:
    if not _ID.match(value or ""):
        raise HTTPException(status_code=404, detail="Not found")
    return value


def _meta(user_dir: Path, doc_id: str) -> dict:
    path = user_dir / f"{doc_id}.json"
    if not path.is_file() or not (user_dir / f"{doc_id}.pdf").is_file():
        raise HTTPException(status_code=404, detail="That document is no longer available. Upload it again.")
    return json.loads(path.read_text(encoding="utf-8"))


async def _save(upload: UploadFile, target: Path) -> int:
    total = 0
    try:
        with target.open("wb") as handle:
            while chunk := await upload.read(_CHUNK):
                total += len(chunk)
                if total > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=(
                        f"'{upload.filename}' is over the {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit."))
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return total


# ── capabilities ───────────────────────────────────────────────────────────────────────────────────────────────────

def _ocr_config():
    from app.services.lc_stamper.ocr import OcrConfig
    online = config.LC_OCR_ONLINE_ENABLED and bool(config.LC_OCR_ONLINE_KEY)
    return OcrConfig(online_url=config.LC_OCR_ONLINE_URL if online else "",
                     online_key=config.LC_OCR_ONLINE_KEY if online else "")


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user)):
    from app.services.lc_stamper import ocr
    return {
        "online_ocr": config.LC_OCR_ONLINE_ENABLED and bool(config.LC_OCR_ONLINE_KEY),
        "offline_ocr": ocr.offline_available(),
        "workers": getattr(jobs, "_CPU_POOL_WORKERS", 1),
        "max_upload_mb": config.MAX_UPLOAD_BYTES // (1024 * 1024),
        "retention_hours": config.LC_STAMPER_RETENTION_HOURS,
    }


# ── documents ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _document_row(meta: dict) -> dict:
    return {key: meta[key] for key in ("id", "name", "pages", "size", "text_pages", "sampled")}


def _sample_text_pages(path: Path, pages: int) -> tuple:
    """How many of up to five pages (spread over the document) have a text layer - tells a scan from a system PDF."""
    import pymupdf as fitz
    picks = sorted({0, pages // 4, pages // 2, (3 * pages) // 4, pages - 1})
    picks = [p for p in picks if 0 <= p < pages]
    with fitz.open(str(path)) as doc:
        with_text = sum(1 for p in picks if len(doc.load_page(p).get_text().strip()) >= 40)
    return with_text, len(picks)


@router.post("/documents")
async def upload_documents(files: list[UploadFile] = File(...), user: User = Depends(get_current_user)):
    from app.services.lc_stamper.engine import BatchError, pdf_pages
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one PDF.")
    user_dir = _user_dir(user)
    _purge_old(user_dir)
    if len(list(user_dir.glob("*.pdf"))) + len(files) > _MAX_DOCUMENTS:
        raise HTTPException(status_code=400, detail=f"At most {_MAX_DOCUMENTS} documents at a time.")
    added, problems = [], []
    for upload in files:
        name = Path(upload.filename or "document.pdf").name
        doc_id = uuid.uuid4().hex
        target = user_dir / f"{doc_id}.pdf"
        try:
            size = await _save(upload, target)
            pages = pdf_pages(str(target), name)
            text_pages, sampled = _sample_text_pages(target, pages)
        except HTTPException:
            raise
        except BatchError as exc:
            target.unlink(missing_ok=True)
            problems.append({"name": name, "error": str(exc)})
            continue
        meta = {"id": doc_id, "name": name, "pages": pages, "size": size, "text_pages": text_pages,
                "sampled": sampled, "uploaded": time.time()}
        (user_dir / f"{doc_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        added.append(_document_row(meta))
    return {"documents": added, "problems": problems}


@router.get("/documents")
def list_documents(user: User = Depends(get_current_user)):
    user_dir = _user_dir(user)
    _purge_old(user_dir)
    rows = []
    for path in sorted(user_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if path.name.startswith("list_") or not (user_dir / f"{path.stem}.pdf").is_file():
            continue
        try:
            rows.append(_document_row(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, KeyError):
            continue
    return {"documents": rows}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: User = Depends(get_current_user)):
    user_dir = _user_dir(user)
    _check_id(doc_id)
    (user_dir / f"{doc_id}.pdf").unlink(missing_ok=True)
    (user_dir / f"{doc_id}.json").unlink(missing_ok=True)
    return {"ok": True}


@router.get("/documents/{doc_id}/pages/{page}")
def page_image(doc_id: str, page: int, zoom: float = Query(1.2, ge=0.2, le=4.0), user: User = Depends(get_current_user)):
    from app.services.lc_stamper import stamping
    user_dir = _user_dir(user)
    meta = _meta(user_dir, _check_id(doc_id))
    if not 1 <= page <= meta["pages"]:
        raise HTTPException(status_code=404, detail="That page does not exist.")
    try:
        png, geometry = stamping.page_png(str(user_dir / f"{doc_id}.pdf"), page - 1, zoom)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"The page could not be shown ({exc.__class__.__name__}).") from exc
    return Response(png, media_type="image/png", headers={
        "Cache-Control": "private, max-age=300", "X-Page-Width-Pt": f"{geometry.width:.2f}",
        "X-Page-Height-Pt": f"{geometry.height:.2f}", "X-Page-Rotation": str(geometry.rotation),
        "Access-Control-Expose-Headers": "X-Page-Width-Pt, X-Page-Height-Pt, X-Page-Rotation"})


# ── the invoice list ───────────────────────────────────────────────────────────────────────────────────────────────

def _list_summary(list_id: str, info) -> dict:
    return {
        "id": list_id, "rows": info.rows, "unique": len(info.targets), "duplicates": info.duplicates,
        "has_dates": info.has_dates, "has_amounts": info.has_amounts, "columns": info.columns,
        "invoice_column": info.invoice_column, "warnings": info.warnings,
        "sample": [{"invoice": t.invoice, "date": t.date.isoformat() if t.date else None, "amount": t.amount}
                   for t in info.targets[:8]],
    }


def _store_list(user: User, info) -> dict:
    from app.services.lc_stamper import targets as target_tools
    if not info.targets:
        raise HTTPException(status_code=400, detail="No invoice numbers were found in the list.")
    list_id = uuid.uuid4().hex
    (_user_dir(user) / f"list_{list_id}.json").write_text(json.dumps(target_tools.to_json(info)), encoding="utf-8")
    return _list_summary(list_id, info)


@router.post("/list")
async def upload_list(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    from app.services.lc_stamper import targets as target_tools
    name = Path(file.filename or "list.xlsx").name
    if not name.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="The invoice list must be an Excel (.xlsx) or CSV file.")
    temp = _user_dir(user) / f"tmp_{uuid.uuid4().hex}{Path(name).suffix.lower()}"
    try:
        await _save(file, temp)
        try:
            info = target_tools.load_list(str(temp))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        temp.unlink(missing_ok=True)
    return {**_store_list(user, info), "name": name}


@router.post("/list/paste")
def paste_list(body: PasteIn, user: User = Depends(get_current_user)):
    from app.services.lc_stamper import targets as target_tools
    return {**_store_list(user, target_tools.parse_pasted(body.text)), "name": "Typed invoice numbers"}


def _load_list(user_dir: Path, list_id: Optional[str]):
    from app.services.lc_stamper import targets as target_tools
    if not list_id:
        return None
    path = user_dir / f"list_{_check_id(list_id)}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The invoice list is no longer available. Upload it again.")
    return target_tools.from_json(json.loads(path.read_text(encoding="utf-8")))


@router.get("/templates/{kind}")
def template(kind: str, user: User = Depends(get_current_user)):
    from app.services.lc_stamper import targets as target_tools
    if kind not in ("full", "filter"):
        raise HTTPException(status_code=404, detail="Not found")
    path = _user_dir(user) / f"template_{kind}_{uuid.uuid4().hex[:8]}.xlsx"
    target_tools.create_template(str(path), kind)
    name = "LC_Stamper_Invoice_List_Template.xlsx" if kind == "full" else "LC_Stamper_Filter_Only_List_Template.xlsx"
    return FileResponse(str(path), filename=name, media_type=_XLSX)


# ── the stamp, as it will be written ───────────────────────────────────────────────────────────────────────────────

@router.post("/stamp-preview")
def stamp_preview(body: StampPreviewIn, user: User = Depends(get_current_user)):
    from app.services.lc_stamper import stamping, targets as target_tools
    from app.services.lc_stamper.models import StampSpec
    info = _load_list(_user_dir(user), body.list_id)
    sample = info.targets[0] if info and info.targets else None
    text = target_tools.stamp_text(body.stamp.text, sample, 1, body.document_name)
    spec = StampSpec(text, body.stamp.font_size, tuple(body.stamp.color), body.stamp.rel_x, body.stamp.rel_y,
                     body.stamp.bold)
    png, margin, (width, height), (box_w, box_h) = stamping.stamp_png(spec, body.zoom)
    return {"png": base64.b64encode(png).decode(), "margin": margin, "width": width, "height": height,
            "box_w": box_w, "box_h": box_h, "text": text}


# ── the run ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _run_job(owner_id: int, documents: list, list_id: Optional[str], mode: str, output: str, rules: dict,
             stamp: dict, progress_cb=None) -> dict:
    """The background job. ``documents`` is [[id, name], ...] of the owner's staged PDFs."""
    from app.services.lc_stamper import engine, targets as target_tools
    from app.services.lc_stamper.models import Rules, StampSpec

    user_dir = _ROOT / str(owner_id)
    docs = []
    for doc_id, name in documents:
        path = user_dir / f"{_check_id(doc_id)}.pdf"
        if not path.is_file():
            raise JobUserError(f"\"{name}\" is no longer available. Upload it again.")
        docs.append(engine.Document(name, str(path)))
    info = None
    if list_id:
        path = user_dir / f"list_{_check_id(list_id)}.json"
        if not path.is_file():
            raise JobUserError("The invoice list is no longer available. Upload it again.")
        info = target_tools.from_json(json.loads(path.read_text(encoding="utf-8")))

    cfg = engine.BatchConfig(
        documents=docs, out_dir=str(SCRATCH_DIR / f"lc-out-{uuid.uuid4().hex}"),
        targets=info.targets if info else [], rules=Rules(**rules), mode=mode,
        stamp=StampSpec(stamp["text"], stamp["font_size"], tuple(stamp["color"]), stamp["rel_x"], stamp["rel_y"],
                        stamp["bold"]),
        output=output, ocr=_ocr_config(),
    )
    progress = progress_cb or (lambda frac, message: None)
    try:
        result = engine.run_batch(cfg, jobs.cpu_pool(), progress, jobs.cancel_requested)
    except engine.Cancelled:
        jobs.abort_cpu_pool()
        raise JobCancelled("Stopped. Nothing was saved.")
    except engine.BatchError as exc:
        raise JobUserError(str(exc)) from exc
    except Exception:
        jobs.abort_cpu_pool()
        raise
    result["out_dir"] = cfg.out_dir
    return result


@router.post("/run")
def run(body: RunIn, user: User = Depends(get_current_user)):
    user_dir = _user_dir(user)
    documents = []
    for doc_id in body.document_ids:
        meta = _meta(user_dir, _check_id(doc_id))
        documents.append([doc_id, meta["name"]])
    info = _load_list(user_dir, body.list_id)
    if body.mode == "filter" and info is None:
        raise HTTPException(status_code=400, detail="Filter only needs an invoice list: upload one or type the numbers.")
    if body.mode == "stamp" and not body.stamp.text.strip():
        raise HTTPException(status_code=400, detail="Type the text to stamp, or switch to Filter only.")
    if info is not None:
        if body.rules.match_dates and not info.has_dates:
            raise HTTPException(status_code=400, detail="The list has no invoice dates, so dates cannot be matched.")
        if body.rules.match_amounts and not info.has_amounts:
            raise HTTPException(status_code=400, detail="The list has no amounts, so amounts cannot be matched.")
    elif body.rules.match_dates or body.rules.match_amounts:
        raise HTTPException(status_code=400, detail="Dates and amounts can only be matched with an invoice list.")
    job_id = submit_job(
        _run_job, user.id, documents, body.list_id, body.mode, body.output, body.rules.model_dump(),
        body.stamp.model_dump(), owner_id=user.id,
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user: User = Depends(get_current_user)):
    job = cancel_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── downloads ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _finished(job_id: str, user: User) -> dict:
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="The run is not finished yet.")
    return job.get("result") or {}


def _inside_outputs(result: dict, path: str) -> Path:
    out_dir = Path(result.get("out_dir", "")).resolve()
    target = Path(path).resolve()
    if not target.is_file() or out_dir not in target.parents or not out_dir.name.startswith("lc-out-"):
        raise HTTPException(status_code=404, detail="The file is no longer available. Run it again.")
    return target


@router.get("/download/{job_id}/pdf/{index}")
def download_pdf(job_id: str, index: int, user: User = Depends(get_current_user)):
    result = _finished(job_id, user)
    outputs = result.get("outputs") or []
    if not 0 <= index < len(outputs):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(_inside_outputs(result, outputs[index]["path"])), filename=outputs[index]["name"],
                        media_type="application/pdf")


@router.get("/download/{job_id}/report")
def download_report(job_id: str, user: User = Depends(get_current_user)):
    result = _finished(job_id, user)
    if not result.get("report_path"):
        raise HTTPException(status_code=404, detail="This run has no report.")
    return FileResponse(str(_inside_outputs(result, result["report_path"])), filename=result["report_name"],
                        media_type=_XLSX)


@router.get("/download/{job_id}/zip")
def download_zip(job_id: str, user: User = Depends(get_current_user)):
    result = _finished(job_id, user)
    files = [(o["name"], _inside_outputs(result, o["path"])) for o in result.get("outputs") or []]
    if result.get("report_path"):
        files.append((result["report_name"], _inside_outputs(result, result["report_path"])))
    if not files:
        raise HTTPException(status_code=404, detail="There is nothing to download.")
    bundle = Path(result["out_dir"]) / "LC_Stamper_Output.zip"
    if not bundle.is_file():
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as archive:
            for name, path in files:
                archive.write(path, name)
    return FileResponse(str(bundle), filename="LC_Stamper_Output.zip", media_type="application/zip")
