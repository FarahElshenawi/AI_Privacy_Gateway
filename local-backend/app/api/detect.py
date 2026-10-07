"""POST /detect — detect PII in text (no masking).

Runs the detection pipeline and returns entities found.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel

from app.pipeline import engine
from app.security.origin_check import check_origin
from app.security.auth import verify_token

router = APIRouter()


class DetectRequest(BaseModel):
    text: str
    entity_schema: list[str] | None = None


class DetectResponse(BaseModel):
    entities: list[dict]
    count: int
    degraded: bool = False
    blocked: bool = False
    coverage_complete: bool = True
    uncovered_labels: list[str] = []
    degraded_reasons: list[str] = []


@router.post("/detect", response_model=DetectResponse)
def detect_pii(
    request: Request,
    body: DetectRequest,
    authorization: str | None = Header(None),
):
    check_origin(request)

    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")

    if len(body.text) > engine.MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail=f"Text longer than {engine.MAX_TEXT_CHARS} characters")
    result = engine.detect(body.text)

    entities = [
        {"type": s.label, "start": s.start, "end": s.end,
         "confidence": s.score, "source": s.source, "validated": s.validated}
        for s in result.spans
    ]

    return DetectResponse(
        entities=entities,
        count=len(entities),
        degraded=result.degraded,
        blocked=result.blocked,
        coverage_complete=not result.degraded,
        uncovered_labels=sorted(result.uncovered_labels),
        degraded_reasons=engine.degraded_reasons(result),
    )
