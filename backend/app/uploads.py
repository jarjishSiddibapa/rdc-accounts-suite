"""Safe streaming helpers shared by file-upload endpoints."""

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app import config

_CHUNK_SIZE = 1024 * 1024


async def save_upload(upload: UploadFile, destination: Path) -> Path:
    safe_name = Path(upload.filename or "upload").name or "upload"
    target = destination / f"{uuid.uuid4()}_{safe_name}"
    total = 0
    try:
        with target.open("wb") as output:
            while chunk := await upload.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File '{safe_name}' exceeds the "
                            f"{config.MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload limit."
                        ),
                    )
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target
