"""POST /demask — restore real values in LLM response.

Uses dlp_core.Demasker: single regex pass, longest fake first, word-boundary guards.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

from app.pipeline.engine import _demasker
from app.security.origin_check import check_origin
from app.security.auth import verify_token

router = APIRouter()


class DemaskRequest(BaseModel):
    text: str
    conversation_id: str = "default"


class DemaskResponse(BaseModel):
    restored_text: str
    replacements_made: int


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

    restored_text, count = _demasker.restore(body.text, body.conversation_id)

    return DemaskResponse(
        restored_text=restored_text,
        replacements_made=count,
    )
