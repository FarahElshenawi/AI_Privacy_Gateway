"""POST /process_file — mask an uploaded file (text, PDF, Word, Excel) and return it.

Offset-based and fail-closed; see app/multimodal/pipeline.py. The masked file is deleted
after the response is sent. Coverage state is reported in response headers so the extension
can warn the user.
"""
from __future__ import annotations

import os
import re
import tempfile
import time

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app import cloud_sync
from app.multimodal.pipeline import MultimodalPipeline
from app.pipeline.engine import FILE_STRICT_DEFAULT
from app.security.auth import verify_token
from app.security.origin_check import check_origin

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _cleanup(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


@router.post("/process_file")
async def process_file(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: str = Form("default"),
    authorization: str | None = Header(None),
):
    check_origin(request)
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    suffix = os.path.splitext(file.filename or "")[1][:10]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        size = 0
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                tmp.close()
                _cleanup(tmp.name)
                raise HTTPException(status_code=413, detail="File too large")
            tmp.write(chunk)
        tmp_path = tmp.name
    out_path = tmp_path + "_masked" + suffix

    t0 = time.perf_counter()
    try:
        result = await run_in_threadpool(MultimodalPipeline(strict=FILE_STRICT_DEFAULT).process, tmp_path, out_path, conversation_id)
    except Exception:
        cloud_sync.record_event("fail_closed", entity_types={"PROCESSING_ERROR": 1}, entity_count=1,
                                latency_ms=(time.perf_counter() - t0) * 1e3, conversation_id=conversation_id)
        _cleanup(tmp_path, out_path)
        raise HTTPException(status_code=500, detail="Processing failed")
    _cleanup(tmp_path)

    elapsed_ms = (time.perf_counter() - t0) * 1e3
    if not result["success"]:
        reason = re.sub(r"[^A-Z0-9_]", "_", str(result["error"] or "UNKNOWN").split(":")[0].upper())[:40]
        cloud_sync.record_event("fail_closed", entity_types={reason: 1}, entity_count=1,
                                latency_ms=elapsed_ms, conversation_id=conversation_id)
        _cleanup(out_path)
        detail = {"error": result["error"], "blockers": result["blockers"], "warnings": result["warnings"],
                  "leak_types": sorted({l.get("type", "") for l in result["leaks"]})}
        raise HTTPException(status_code=422 if (result["blockers"] or result["leaks"]) else 400, detail=detail)

    cloud_sync.record_event("file", entity_count=result["replacements_made"], latency_ms=elapsed_ms,
                            conversation_id=conversation_id)
    headers = {"X-DLP-Degraded": str(result["degraded"]).lower(),
               "X-DLP-Uncovered-Labels": ",".join(result["uncovered_labels"]),
               "X-DLP-Warnings": ",".join(result["warnings"]),
               "X-DLP-Replacements": str(result["replacements_made"])}
    return FileResponse(path=out_path, filename=f"masked_{file.filename}", headers=headers,
                        media_type=file.content_type or "application/octet-stream",
                        background=BackgroundTask(_cleanup, out_path))
