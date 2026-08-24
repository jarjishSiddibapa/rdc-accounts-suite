"""DMS document-fetch tool routes."""

import io
import time
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app import auth, config
from app.models import User
from app.permissions import require_app_access
from app.services.dms import fetcher

router = APIRouter(
    prefix="/api/tools/dms", tags=["dms"],
    dependencies=[Depends(require_app_access("dms"))],
)

_FETCHED_TTL_SECONDS = 30 * 60

# token -> {"path": str, "mime": str, "fname": str, "ts": float, "owner_id": int}
_fetched: dict[str, dict] = {}
_fetched_lock = threading.Lock()


class FetchBody(BaseModel):
    url: str


def _prune_expired() -> None:
    now = time.time()
    expired = [token for token, entry in _fetched.items() if now - entry["ts"] > _FETCHED_TTL_SECONDS]
    for token in expired:
        entry = _fetched.pop(token)
        Path(entry["path"]).unlink(missing_ok=True)


@router.post("/fetch", dependencies=[Depends(auth.get_current_user)])
def fetch(body: FetchBody, user: User = Depends(auth.get_current_user)):
    res = fetcher.fetch_document(body.url)

    token = None
    if res.blob:
        token = str(uuid.uuid4())
        safe_name = Path(res.fname or "document").name or "document"
        stored_path = config.SCRATCH_DIR / f"dms_{token}"
        stored_path.write_bytes(res.blob)
        with _fetched_lock:
            _prune_expired()
            _fetched[token] = {
                "path": str(stored_path),
                "mime": res.mime or "application/octet-stream",
                "fname": safe_name[:200],
                "ts": time.time(),
                "owner_id": user.id,
            }

    return {
        "error": res.error,
        "error_detail": res.error_detail,
        "mime": res.mime,
        "fname": res.fname,
        "final_url": res.final_url,
        "steps": res.steps,
        "size": len(res.blob) if res.blob else 0,
        "token": token,
    }


@router.get("/download/{token}", dependencies=[Depends(auth.get_current_user)])
def download(token: str, user: User = Depends(auth.get_current_user)):
    with _fetched_lock:
        _prune_expired()
        entry = _fetched.get(token)
        if entry is not None and entry["owner_id"] != user.id:
            entry = None
    if entry is None:
        raise HTTPException(status_code=404, detail="Token not found or expired")

    path = Path(entry["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document is no longer available")
    return FileResponse(
        path=str(path),
        filename=entry["fname"],
        media_type=entry["mime"],
    )


def _owned_entry(token: str, user: User) -> dict:
    with _fetched_lock:
        _prune_expired()
        entry = _fetched.get(token)
        if entry is not None and entry["owner_id"] != user.id:
            entry = None
    if entry is None:
        raise HTTPException(status_code=404, detail="Token not found or expired")
    path = Path(entry["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document is no longer available")
    return entry


@router.get("/view/{token}", dependencies=[Depends(auth.get_current_user)])
def view(token: str, user: User = Depends(auth.get_current_user)):
    """Serve a fetched document inline for the desktop-style viewer/open action."""
    entry = _owned_entry(token, user)
    path = Path(entry["path"])
    return Response(
        content=path.read_bytes(),
        media_type=entry["mime"],
        headers={
            "Content-Disposition": f'inline; filename="{Path(entry["fname"]).name}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/pdf/{token}", dependencies=[Depends(auth.get_current_user)])
def save_as_pdf(token: str, user: User = Depends(auth.get_current_user)):
    """Return the original PDF or convert a fetched image to PDF, matching
    the desktop app's Save as PDF action."""
    entry = _owned_entry(token, user)
    source = Path(entry["path"])
    mime = (entry.get("mime") or "").lower()

    if mime == "application/pdf":
        pdf_bytes = source.read_bytes()
    elif mime.startswith("image/"):
        try:
            from PIL import Image

            with Image.open(source) as image:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, "PDF", resolution=150.0)
                pdf_bytes = output.getvalue()
        except Exception as exc:  # noqa: BLE001 - conversion boundary
            raise HTTPException(status_code=422, detail=f"PDF conversion failed: {exc}") from exc
    else:
        raise HTTPException(status_code=400, detail="Only images and PDF documents can be saved as PDF")

    filename = f"{Path(entry['fname']).stem or 'document'}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
