"""POST /mask — mask PII in text.

Detects PII, generates (original, replacement) pairs, stores them in the
vault, and returns the masked text.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

from app.pipeline.detection import detect
from app.pipeline.masking import mask_text
from app.pipeline.residual_scanner import scan
from app.security.origin_check import check_origin
from app.security.auth import verify_token

router = APIRouter()


class MaskRequest(BaseModel):
    text: str
    conversation_id: str = "default"
    entity_schema: list[str] | None = None


class MaskResponse(BaseModel):
    masked_text: str
    pairs: list[tuple[str, str]]
    entities_found: int
    leaks: list[dict]
    safe_to_send: bool


@router.post("/mask", response_model=MaskResponse)
async def mask_pii(
    request: Request,
    body: MaskRequest,
    authorization: str | None = Header(None),
):
    """Detect, mask, and scan PII in text."""
    check_origin(request)

    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    # Step 1: Detect
    entities = detect(body.text, body.entity_schema)

    # Step 2: Mask (generates pairs + stores in vault)
    masked_text, pairs = mask_text(body.text, entities, body.conversation_id)

    # Step 3: Residual scan (last gate)
    leaks = scan(masked_text)

    return MaskResponse(
        masked_text=masked_text,
        pairs=pairs,
        entities_found=len(entities),
        leaks=leaks,
        safe_to_send=len(leaks) == 0,
    )
