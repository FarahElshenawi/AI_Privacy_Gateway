"""POST /demask — restore real values in LLM response.

Uses dlp_core.Demasker: single regex pass, longest fake first, word-boundary guards.

Every demask call is the most security-sensitive operation in the system: it returns
real PII. So every call:
  - emits a metadata-only DemaskAuditRecord (no text, no value, no surrogate)
  - is rate-limited per conversation (default 100 calls / 10 minutes) to prevent
    a runaway or compromised client from bulk-unmasking an entire vault
  - validates conversation_id format to prevent vault cross-contamination
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel, field_validator

from app.pipeline.engine import _demasker
from app.security.origin_check import check_origin
from app.security.auth import verify_token
from dlp_core.audit import build_demask_audit_record, emit_demask_audit

router = APIRouter()

# Per-conversation rate limit: N calls per window. A conversation is the natural
# unit because the vault is per-conversation — an attacker who can call /demask
# freely on one conversation could walk every fake back to its real value.
_DEMASK_MAX_CALLS = 100
_DEMASK_WINDOW_S = 600   # 10 minutes
_DEMASK_CALLS: dict[str, deque[float]] = defaultdict(deque)
_DEMASK_LOCK_TICKS: dict[str, float] = {}

# Conversation IDs are used as vault keys. Accept only safe characters to prevent
# path traversal in any future persistent vault backing, and to keep log lines
# parseable (no whitespace, no quotes).
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class DemaskRequest(BaseModel):
    text: str
    conversation_id: str = "default"

    @field_validator("conversation_id")
    @classmethod
    def _validate_conv_id(cls, v: str) -> str:
        if not v or not _CONV_ID_RE.match(v):
            raise ValueError("conversation_id must match [A-Za-z0-9_.:-]{1,64}")
        return v


class DemaskResponse(BaseModel):
    restored_text: str
    replacements_made: int


def _check_rate_limit(conversation_id: str) -> None:
    """Per-conversation sliding window. Raises 429 if exceeded."""
    now = time.monotonic()
    window = _DEMASK_CALLS[conversation_id]
    # Drop entries outside the window
    while window and window[0] < now - _DEMASK_WINDOW_S:
        window.popleft()
    if len(window) >= _DEMASK_MAX_CALLS:
        raise HTTPException(
            status_code=429,
            detail=f"Demask rate limit exceeded for conversation '{conversation_id}': "
                   f"max {_DEMASK_MAX_CALLS} calls per {_DEMASK_WINDOW_S}s",
        )
    window.append(now)


@router.post("/demask", response_model=DemaskResponse)
def demask_pii(
    request: Request,
    body: DemaskRequest,
    authorization: str | None = Header(None),
):
    check_origin(request)

    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    _check_rate_limit(body.conversation_id)

    restored_text, count = _demasker.restore(body.text, body.conversation_id)

    # Audit every demask call. The record contains metadata only: conversation_id,
    # replacement count, and input text length. NEVER the text, the restored
    # values, or any substring of either.
    emit_demask_audit(build_demask_audit_record(
        conversation_id=body.conversation_id,
        replacements_made=count,
        text_length=len(body.text),
    ))

    return DemaskResponse(
        restored_text=restored_text,
        replacements_made=count,
    )
