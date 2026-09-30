"""POST /demask — restore real values in LLM response.

Searches the LLM response text for fake values and replaces them with
the real values from the vault.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

from app.vault.store import get_vault
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
async def demask_pii(
    request: Request,
    body: DemaskRequest,
    authorization: str | None = Header(None),
):
    """Restore real values in the LLM response."""
    check_origin(request)

    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    vault = get_vault()
    original_text = body.text
    restored_text = vault.restore_text(body.conversation_id, body.text)

    # Count how many replacements were made
    replacements_made = sum(
        1 for fake in vault.get_conversation(body.conversation_id).get_all_fakes()
        if fake in original_text
    ) if vault.get_conversation(body.conversation_id) else 0

    return DemaskResponse(
        restored_text=restored_text,
        replacements_made=replacements_made,
    )
