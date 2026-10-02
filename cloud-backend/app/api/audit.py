"""Audit ingestion — metadata only, never prompt content.

Endpoints:
  POST   /api/audit            — submit an audit event
  GET    /api/audit             — list events (with filters)
  GET    /api/audit/stats       — aggregated statistics for dashboard
  DELETE /api/audit/{id}        — delete an event (GDPR right to erasure)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func

from app.db import get_session, AuditEvent, init_db

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEventCreate(BaseModel):
    org_id: int = 1
    endpoint_id: Optional[int] = None
    event_type: str  # "mask", "detect", "file", "fail_closed"
    entity_types: Optional[dict] = None  # {"PERSON": 3, "EMAIL": 2}
    entity_count: int = 0
    latency_ms: Optional[int] = None
    conversation_id: Optional[str] = None


class AuditEventResponse(BaseModel):
    id: int
    event_type: str
    entity_types: Optional[dict]
    entity_count: int
    latency_ms: Optional[int]
    timestamp: datetime


def get_db():
    engine = init_db()
    db = get_session(engine)
    try:
        yield db
    finally:
        db.close()


@router.post("", response_model=AuditEventResponse, status_code=201)
async def submit_audit(body: AuditEventCreate, db=Depends(get_db)):
    """Submit an audit event from a local backend.

    This endpoint accepts METADATA ONLY:
    - entity types and counts (e.g. {"PERSON": 3, "EMAIL": 2})
    - latency (P95 in ms)
    - event type (mask/detect/file/fail_closed)

    It NEVER accepts prompt content, masked or unmasked.
    """
    event = AuditEvent(
        org_id=body.org_id,
        endpoint_id=body.endpoint_id,
        event_type=body.event_type,
        entity_types=body.entity_types,
        entity_count=body.entity_count,
        latency_ms=body.latency_ms,
        conversation_id=body.conversation_id,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("", response_model=list[AuditEventResponse])
async def list_audit(
    org_id: int = 1,
    event_type: Optional[str] = None,
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=1000),
    db=Depends(get_db),
):
    """List audit events with optional filters."""
    since = datetime.utcnow() - timedelta(hours=hours)
    query = db.query(AuditEvent).filter(
        AuditEvent.org_id == org_id,
        AuditEvent.timestamp >= since,
    )
    if event_type:
        query = query.filter(AuditEvent.event_type == event_type)
    return query.order_by(AuditEvent.timestamp.desc()).limit(limit).all()


@router.get("/stats")
async def get_stats(org_id: int = 1, hours: int = Query(24, ge=1, le=720), db=Depends(get_db)):
    """Get aggregated statistics for the dashboard.

    Returns counts, entity type breakdown, latency percentiles,
    and fail-closed events.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    
    total_events = db.query(AuditEvent).filter(
        AuditEvent.org_id == org_id,
        AuditEvent.timestamp >= since,
    ).count()

    # Breakdown by event type
    type_counts = (
        db.query(AuditEvent.event_type, func.count(AuditEvent.id))
        .filter(AuditEvent.org_id == org_id, AuditEvent.timestamp >= since)
        .group_by(AuditEvent.event_type)
        .all()
    )

    # Fail-closed events
    fail_closed = db.query(AuditEvent).filter(
        AuditEvent.org_id == org_id,
        AuditEvent.timestamp >= since,
        AuditEvent.event_type == "fail_closed",
    ).count()

    # Average latency (for mask events)
    latency_avg = (
        db.query(func.avg(AuditEvent.latency_ms))
        .filter(
            AuditEvent.org_id == org_id,
            AuditEvent.timestamp >= since,
            AuditEvent.event_type == "mask",
            AuditEvent.latency_ms.isnot(None),
        )
        .scalar()
    )

    # Total entities masked
    total_entities = (
        db.query(func.sum(AuditEvent.entity_count))
        .filter(
            AuditEvent.org_id == org_id,
            AuditEvent.timestamp >= since,
            AuditEvent.event_type == "mask",
        )
        .scalar()
    )

    # Entity type breakdown
    entity_breakdown = {}
    events = db.query(AuditEvent).filter(
        AuditEvent.org_id == org_id,
        AuditEvent.timestamp >= since,
        AuditEvent.event_type == "mask",
    ).all()
    for e in events:
        if e.entity_types:
            for etype, count in e.entity_types.items():
                entity_breakdown[etype] = entity_breakdown.get(etype, 0) + count

    return {
        "time_window_hours": hours,
        "total_events": total_events,
        "by_event_type": {k: v for k, v in type_counts},
        "fail_closed_events": fail_closed,
        "avg_latency_ms": round(latency_avg, 1) if latency_avg else None,
        "total_entities_masked": total_entities or 0,
        "entity_type_breakdown": entity_breakdown,
    }


@router.delete("/{event_id}", status_code=204)
async def delete_audit(event_id: int, db=Depends(get_db)):
    """Delete an audit event (GDPR right to erasure)."""
    event = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    db.delete(event)
    db.commit()
