"""Audit ingestion — metadata only, never prompt content. Authenticated and org-scoped.

Endpoints (all require an API key; the organization is the key's organization):
  POST   /api/audit            — submit an audit event
  GET    /api/audit            — list events (with filters)
  GET    /api/audit/stats      — aggregated statistics for dashboard
  DELETE /api/audit/{id}       — delete an event (GDPR right to erasure)

"Never content" is enforced by the request schema, not just by convention: the event model
accepts ONLY the fields below (extra fields are rejected with 422), entity_types is a
{LABEL: count} map of non-negative integers, and every string is length/charset-limited.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer, field_validator
from sqlalchemy import func

from app.auth import require_org
from app.db import AuditEvent, Endpoint, Organization, get_db

router = APIRouter(prefix="/api/audit", tags=["audit"])

Label = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
Count = Annotated[int, Field(ge=0, le=10_000_000)]


class AuditEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a `text`/`prompt` field can't be smuggled in

    endpoint_id: Optional[int] = None
    event_type: Literal["mask", "detect", "file", "fail_closed", "demask"]
    entity_types: Optional[dict[Label, Count]] = None  # {"PERSON": 3, "EMAIL": 2}
    entity_count: Count = 0
    latency_ms: Optional[Annotated[int, Field(ge=0, le=3_600_000)]] = None
    conversation_id: Optional[Annotated[str, StringConstraints(max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]] = None

    @field_validator("entity_types")
    @classmethod
    def _bounded(cls, v):
        if v is not None and len(v) > 64:
            raise ValueError("too many entity types")
        return v


class AuditEventResponse(BaseModel):
    id: int
    event_type: str
    entity_types: Optional[dict]
    entity_count: int
    latency_ms: Optional[int]
    timestamp: datetime

    @field_serializer("timestamp")
    def _utc(self, v: datetime) -> str:
        """Stored as naive UTC; send it WITH a zone so clients don't read it as local time."""
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat().replace("+00:00", "Z")


@router.post("", response_model=AuditEventResponse, status_code=201)
def submit_audit(body: AuditEventCreate, org: Organization = Depends(require_org), db=Depends(get_db)):
    """Submit an audit event from a local backend.

    Accepts METADATA ONLY: entity types and counts, latency, event type.
    It NEVER accepts prompt content, masked or unmasked.
    """
    if body.endpoint_id is not None:
        owned = db.query(Endpoint).filter(Endpoint.id == body.endpoint_id, Endpoint.org_id == org.id).first()
        if not owned:
            raise HTTPException(status_code=422, detail="Unknown endpoint_id")
    event = AuditEvent(
        org_id=org.id,
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
def list_audit(
    event_type: Optional[str] = None,
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=1000),
    org: Organization = Depends(require_org),
    db=Depends(get_db),
):
    """List this organization's audit events with optional filters."""
    since = datetime.utcnow() - timedelta(hours=hours)
    query = db.query(AuditEvent).filter(
        AuditEvent.org_id == org.id,
        AuditEvent.timestamp >= since,
    )
    if event_type:
        query = query.filter(AuditEvent.event_type == event_type)
    return query.order_by(AuditEvent.timestamp.desc()).limit(limit).all()


@router.get("/stats")
def get_stats(hours: int = Query(24, ge=1, le=720),
              org: Organization = Depends(require_org), db=Depends(get_db)):
    """Aggregated statistics for the dashboard.

    Returns counts, entity type breakdown, latency, and fail-closed events.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    in_window = (AuditEvent.org_id == org.id, AuditEvent.timestamp >= since)

    total_events = db.query(AuditEvent).filter(*in_window).count()

    type_counts = (
        db.query(AuditEvent.event_type, func.count(AuditEvent.id))
        .filter(*in_window)
        .group_by(AuditEvent.event_type)
        .all()
    )

    fail_closed = db.query(AuditEvent).filter(*in_window, AuditEvent.event_type == "fail_closed").count()

    latency_avg = (
        db.query(func.avg(AuditEvent.latency_ms))
        .filter(*in_window, AuditEvent.event_type == "mask", AuditEvent.latency_ms.isnot(None))
        .scalar()
    )

    total_entities = (
        db.query(func.sum(AuditEvent.entity_count))
        .filter(*in_window, AuditEvent.event_type == "mask")
        .scalar()
    )

    entity_breakdown: dict[str, int] = {}
    for e in db.query(AuditEvent).filter(*in_window, AuditEvent.event_type == "mask").all():
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
def delete_audit(event_id: int, org: Organization = Depends(require_org), db=Depends(get_db)):
    """Delete an audit event (GDPR right to erasure)."""
    event = db.query(AuditEvent).filter(AuditEvent.id == event_id, AuditEvent.org_id == org.id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    db.delete(event)
    db.commit()
