"""POST /process_file — process a file upload through the multimodal pipeline.

Receives a file, extracts text, detects PII, masks it, reconstructs the
masked file, and returns it.
"""
from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile, File, Request, Header
from fastapi.responses import FileResponse

from app.multimodal.pipeline import MultimodalPipeline
from app.security.origin_check import check_origin
from app.security.auth import verify_token

router = APIRouter()


@router.post("/process_file")
async def process_file(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: str = "default",
    authorization: str | None = Header(None),
):
    """Process an uploaded file through the multimodal pipeline.

    1. Save the uploaded file to a temp location
    2. Run: extract → detect → mask → reconstruct
    3. Return the masked file

    The masked file has the same format as the original, but PII is
    replaced with surrogates (Faker) or redacted ([[REDACTED]]).
    """
    check_origin(request)

    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    # Save uploaded file to temp
    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        pipeline = MultimodalPipeline()
        output_path = tmp_path + "_masked"

        result = pipeline.process(tmp_path, output_path, conversation_id=conversation_id)

        if not result["success"]:
            if result.get("leaks"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Fail-closed: residual scanner found {len(result['leaks'])} leaks in masked file"
                )
            raise HTTPException(status_code=400, detail=result.get("error", "Processing failed"))

        return FileResponse(
            path=result["output_path"],
            filename=f"masked_{file.filename}",
            media_type=file.content_type or "application/octet-stream",
        )

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
