"""POST /detect — detect PII in text.

Runs the tiered detection engine (deterministic + semantic) on the
provided text and returns the entities found.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.pipeline.detection import detect
from app.security.origin_check import check_origin
from app.security.auth import verify_token
from fastapi import Request, Header

router = APIRouter()


class DetectRequest(BaseModel):
    text: str
    entity_schema: list[str] | None = None


class DetectResponse(BaseModel):
    entities: list[dict]
    count: int


@router.post("/detect", response_model=DetectResponse)
async def detect_pii(
    request: Request,
    body: DetectRequest,
    authorization: str | None = Header(None),
):
    """Detect PII in the provided text."""
    check_origin(request)

    # Extract token from "Bearer <token>" header
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    entities = detect(body.text, body.entity_schema)
    return DetectResponse(entities=entities, count=len(entities))
