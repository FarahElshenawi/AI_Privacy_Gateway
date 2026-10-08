"""POST /mapping — the fake→real entries of ONE conversation, for local display-time demasking.

Why this exists: the extension restores real values in the ChatGPT/Gemini page as the reply
streams in. Doing that per streamed chunk through /demask would blow its rate limit and add a
round trip per token, so the extension asks for the (small) mapping once per change and replaces
text locally.

It exposes exactly what /demask already can (real values for known fakes), so it carries the
same protections: token + loopback/Origin checks, conversation_id validation, a per-conversation
rate limit, and an audit record (metadata only) whenever entries are actually delivered.

The client sends the vault `version` it already holds; when nothing changed the answer is just
`{changed: false}` — no entries cross the wire and nothing is audited.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.pipeline.engine import _vault
from app.security.auth import verify_token
from app.security.origin_check import check_origin
from dlp_core.audit import build_demask_audit_record, emit_demask_audit

router = APIRouter()

_MAX_CALLS = 600          # version checks are cheap; this only stops a runaway client
_WINDOW_S = 600
_CALLS: dict[str, deque[float]] = defaultdict(deque)
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class MappingRequest(BaseModel):
    conversation_id: str
    since_version: Optional[int] = None

    @field_validator("conversation_id")
    @classmethod
    def _valid(cls, v: str) -> str:
        if not _CONV_ID_RE.match(v):
            raise ValueError("conversation_id must match [A-Za-z0-9_.:-]{1,64}")
        return v


class MappingEntry(BaseModel):
    fake: str
    real: str


class MappingResponse(BaseModel):
    version: int
    changed: bool
    entries: list[MappingEntry] = []


def _rate_limit(conv: str) -> None:
    now = time.monotonic()
    w = _CALLS[conv]
    while w and w[0] < now - _WINDOW_S:
        w.popleft()
    if len(w) >= _MAX_CALLS:
        raise HTTPException(status_code=429, detail="Mapping rate limit exceeded for this conversation")
    w.append(now)


@router.post("/mapping", response_model=MappingResponse)
def get_mapping(request: Request, body: MappingRequest, authorization: str | None = Header(None)):
    check_origin(request)
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    _rate_limit(body.conversation_id)

    version = _vault.version(body.conversation_id)
    if body.since_version is not None and body.since_version == version:
        return MappingResponse(version=version, changed=False)

    entries = [MappingEntry(fake=f, real=r) for f, r in _vault.items(body.conversation_id)]
    if entries:   # delivering real values is a demask-class event: audit it (counts only)
        emit_demask_audit(build_demask_audit_record(
            conversation_id=body.conversation_id, replacements_made=len(entries), text_length=0))
    return MappingResponse(version=version, changed=True, entries=entries)
