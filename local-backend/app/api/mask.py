"""POST /mask — mask PII in text.

DetectionPipeline -> MergeEngine -> OffsetMasker -> residual scan. Fail-closed.

`safe_to_send` means: no residual leak, no blocked detector, AND (coverage is complete OR strict
mode is off). Coverage is always reported (`coverage_complete`, `uncovered_labels`,
`degraded_reasons`) so a client never has to guess what was NOT checked. Strict mode comes from
the request's `strict` field, else the DLP_STRICT environment variable.

The endpoint is a plain `def`: FastAPI runs it in its thread pool, so slow model inference
can't freeze the event loop (and /health) for everyone else.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.pipeline import engine
from app.security.auth import verify_token
from app.security.origin_check import check_origin
from dlp_core.masker import MaskingError
from dlp_core.vault import VaultCapacityError

router = APIRouter()


class MaskRequest(BaseModel):
    text: str
    conversation_id: str = "default"
    entity_schema: list[str] | None = None      # accepted for compatibility, ignored
    strict: bool | None = None


class MaskResponse(BaseModel):
    masked_text: str
    entities_found: int
    entity_types: list[str]
    leaks: list[dict]
    safe_to_send: bool
    degraded: bool = False
    blocked: bool = False
    coverage_complete: bool = True
    uncovered_labels: list[str] = []
    degraded_reasons: list[str] = []
    strict: bool = False


def _auth(request: Request, authorization: str | None) -> None:
    check_origin(request)
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")


@router.post("/mask", response_model=MaskResponse)
def mask_pii(request: Request, body: MaskRequest, authorization: str | None = Header(None)):
    _auth(request, authorization)
    if len(body.text) > engine.MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail=f"Text longer than {engine.MAX_TEXT_CHARS} characters")

    result = engine.detect(body.text)
    if result.blocked:
        raise HTTPException(status_code=503, detail="Detection blocked: critical detector failed")

    try:
        masked = engine._masker.mask(body.text, result.merged, body.conversation_id)
    except (MaskingError, VaultCapacityError):
        raise HTTPException(status_code=503, detail="Masking failed")   # nothing is returned for sending

    leaks = engine.residual_scan(masked.masked_text)
    strict = engine.STRICT_DEFAULT if body.strict is None else body.strict
    coverage_complete = not result.degraded
    return MaskResponse(
        masked_text=masked.masked_text,
        entities_found=len(result.spans),
        entity_types=sorted({s.label for s in result.spans}),
        leaks=leaks,
        safe_to_send=not leaks and (coverage_complete or not strict),
        degraded=result.degraded,
        blocked=False,
        coverage_complete=coverage_complete,
        uncovered_labels=sorted(result.uncovered_labels),
        degraded_reasons=engine.degraded_reasons(result),
        strict=strict,
    )
