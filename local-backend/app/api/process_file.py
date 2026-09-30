"""POST /process_file — process a file upload through the multimodal pipeline.

Receives a file, extracts text, detects PII, masks it, reconstructs the
masked file, and returns it. Fail-closed: if anything goes wrong, or the
residual scan still finds PII, nothing is returned.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Request, Header
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.multimodal.pipeline import MultimodalPipeline
from app.security.origin_check import check_origin
from app.security.auth import verify_token

router = APIRouter()

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", 25)) * 1024 * 1024


def _remove(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


@router.post("/process_file")
async def process_file(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: str = "default",
    authorization: str | None = Header(None),
):
    """Process an uploaded file: extract -> detect -> mask -> reconstruct -> scan."""
    check_origin(request)

    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Never trust the client filename for paths: keep only the extension.
    safe_name = Path(file.filename or "upload").name
    suffix = Path(safe_name).suffix.lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    # Keep the extension: openpyxl and others refuse to open extensionless files.
    output_path = str(Path(tmp_path).with_name(Path(tmp_path).stem + "_masked" + suffix))

    try:
        result = await run_in_threadpool(
            MultimodalPipeline().process, tmp_path, output_path, conversation_id
        )
    except Exception:
        _remove(tmp_path, output_path)
        raise
    _remove(tmp_path)  # original (unmasked) upload is no longer needed

    if not result["success"]:
        _remove(output_path)
        if result.get("leaks"):
            raise HTTPException(
                status_code=422,
                detail=f"Fail-closed: residual scanner found {len(result['leaks'])} leaks in masked file",
            )
        raise HTTPException(status_code=400, detail=result.get("error", "Processing failed"))

    return FileResponse(
        path=result["output_path"],
        filename=f"masked_{safe_name}",
        media_type=file.content_type or "application/octet-stream",
        background=BackgroundTask(_remove, output_path),
    )
