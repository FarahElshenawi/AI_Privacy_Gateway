"""POST /mask — mask PII in text.

Detects PII, generates (original, replacement) pairs, stores them in the
vault, and returns the masked text.

PRIVACY: The response does NOT include `pairs` (which contain real values).
The SW applies masks per-slot by calling /mask once per message-part, so
it doesn't need the pairs returned — it uses the `masked_text` directly.
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
    """Response schema.

    NOTE: `pairs` is intentionally omitted — it contains real PII values.
    The SW applies masks by calling /mask once per message slot and using
    the returned `masked_text` directly, never the pairs.
    """
    masked_text: str
    entities_found: int
    entity_types: list[str]
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

    # Step 2: Mask (generates pairs + stores in vault; pairs are NOT returned)
    masked_text, pairs = mask_text(body.text, entities, body.conversation_id)

    # Step 3: Residual scan (last gate)
    leaks = scan(masked_text)

    # Extract entity TYPES only (safe to expose — no real values)
    entity_types = sorted({e.get("type", "") for e in entities if e.get("type")})

    return MaskResponse(
        masked_text=masked_text,
        entities_found=len(entities),
        entity_types=entity_types,
        leaks=leaks,
        safe_to_send=len(leaks) == 0,
    )
