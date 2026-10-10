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

import re
import time

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

from app import cloud_sync
from app.pipeline import engine
from app.security.auth import verify_token
from app.security.origin_check import check_origin
from dlp_core.masker import MaskingError, RequestBlockedError
from dlp_core.vault import VaultCapacityError

router = APIRouter()

# Conversation IDs are used as vault keys. Match the same pattern as /demask
# to prevent vault cross-contamination and keep audit logs parseable.
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class MaskRequest(BaseModel):
    text: str
    conversation_id: str = "default"
    entity_schema: list[str] | None = None      # accepted for compatibility, ignored
    strict: bool | None = None

    @field_validator("conversation_id")
    @classmethod
    def _validate_conv_id(cls, v: str) -> str:
        if not v or not _CONV_ID_RE.match(v):
            raise ValueError("conversation_id must match [A-Za-z0-9_.:-]{1,64}")
        return v


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


class BlockDetail(BaseModel):
    """Detail returned when a request is blocked by policy (action=BLOCK).

    The entity_type and rule tell the client WHY; no real value is included.
    """
    reason: str = "blocked_by_policy"
    entity_type: str
    rule: str


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

    t0 = time.perf_counter()
    cid = body.conversation_id

    def _closed(reason: str) -> None:
        cloud_sync.record_event("fail_closed", entity_types={reason: 1}, entity_count=1,
                                latency_ms=(time.perf_counter() - t0) * 1e3, conversation_id=cid)

    result = engine.detect(body.text)
    if result.blocked:
        _closed("DETECTION_BLOCKED")
        raise HTTPException(status_code=503, detail="Detection blocked: critical detector failed")

    try:
        masked = engine._masker.mask(body.text, result.merged, body.conversation_id)
    except RequestBlockedError as exc:
        # Policy decided this request must not be sent. Return 422 so the extension
        # can distinguish "blocked by policy" from "masking failed" (503) and from
        # "request was malformed" (422). The detail carries the entity type and rule
        # so the user understands which entity triggered the block.
        _closed("POLICY_BLOCK")
        raise HTTPException(
            status_code=422,
            detail={"reason": "blocked_by_policy", "entity_type": exc.entity_type, "rule": exc.rule},
        )
    except (MaskingError, VaultCapacityError):
        _closed("MASKING_FAILED")
        raise HTTPException(status_code=503, detail="Masking failed")   # nothing is returned for sending

    leaks = engine.residual_scan(masked.masked_text)
    strict = engine.STRICT_DEFAULT if body.strict is None else body.strict
    coverage_complete = not result.degraded
    safe = not leaks and (coverage_complete or not strict)
    if safe:
        counts: dict[str, int] = {}
        for ms in result.merged:
            counts[ms.label] = counts.get(ms.label, 0) + 1
        cloud_sync.record_event("mask", entity_types=counts, entity_count=len(result.merged),
                                latency_ms=(time.perf_counter() - t0) * 1e3, conversation_id=cid)
    else:
        _closed("RESIDUAL_LEAK" if leaks else "DEGRADED_STRICT")
    return MaskResponse(
        masked_text=masked.masked_text,
        entities_found=len(result.spans),
        entity_types=sorted({s.label for s in result.spans}),
        leaks=leaks,
        safe_to_send=safe,
        degraded=result.degraded,
        blocked=False,
        coverage_complete=coverage_complete,
        uncovered_labels=sorted(result.uncovered_labels),
        degraded_reasons=engine.degraded_reasons(result),
        strict=strict,
    )
