"""Endpoint enrollment + heartbeat API.

Endpoints are local-backend installations registered to an organization. Each
endpoint has:
  - an enrollment token (issued once at enrollment, used for nothing else)
  - a hostname (machine name, for fleet display)
  - a version (local-backend version, for compatibility checks)
  - a last_seen timestamp (updated on every heartbeat)

The local backend enrolls on first startup (after the user pastes the org's API key),
then sends a heartbeat every N seconds. The dashboard uses the endpoints table to
show "X devices online, Y devices stale, Z devices running old version".

All routes require the org's API key (via require_org); the endpoint's own
enrollment_token is separate and used only for heartbeats (so a leaked heartbeat
token can't read policies or audit events).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.auth import require_org
from app.db import Endpoint, Organization, get_db
import secrets

router = APIRouter(prefix="/api/endpoints", tags=["endpoints"])

Hostname = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,255}$")]
Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._+~-]{1,32}$")]


class EnrollRequest(BaseModel):
    """A local backend enrolls itself on first startup.

    The caller already has the org API key (sent as X-API-Key). The enrollment
    creates an Endpoint row and returns an enrollment_token that the local backend
    stores for future heartbeats.
    """
    model_config = ConfigDict(extra="forbid")
    hostname: Hostname
    version: Optional[Version] = None


class EnrollResponse(BaseModel):
    endpoint_id: int
    enrollment_token: str
    hostname: str


class HeartbeatRequest(BaseModel):
    """A local backend sends a heartbeat every N seconds.

    The enrollment_token identifies which endpoint this is. The version field
    lets the dashboard track which devices are running old local-backend versions.
    """
    model_config = ConfigDict(extra="forbid")
    enrollment_token: Annotated[str, StringConstraints(min_length=32, max_length=128)]
    version: Optional[Version] = None


class HeartbeatResponse(BaseModel):
    ok: bool
    stale_after_seconds: int


class EndpointInfo(BaseModel):
    """Public-facing info about an endpoint (no tokens)."""
    id: int
    hostname: Optional[str]
    version: Optional[str]
    last_seen: Optional[datetime]
    is_active: bool
    created_at: datetime

    @property
    def is_stale(self) -> bool:
        """An endpoint is stale if last_seen is older than 5 minutes."""
        if self.last_seen is None:
            return True
        return datetime.utcnow() - self.last_seen > timedelta(minutes=5)


STALE_AFTER_S = 300  # 5 minutes — heartbeats expected every 60s


@router.post("/enroll", response_model=EnrollResponse, status_code=201)
def enroll(body: EnrollRequest, org: Organization = Depends(require_org),
           db=Depends(get_db)):
    """Register a new local backend installation.

    Called once on first startup. The local backend stores the returned
    enrollment_token locally and uses it for heartbeats. If the enrollment_token
    is lost, the local backend can re-enroll (creating a new endpoint row);
    the dashboard can clean up stale endpoints.
    """
    token = secrets.token_urlsafe(32)
    endpoint = Endpoint(
        org_id=org.id,
        hostname=body.hostname,
        enrollment_token=token,   # NOTE: in production, store a hash like api_key
        version=body.version,
        last_seen=datetime.utcnow(),
        is_active=True,
    )
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)
    return EnrollResponse(
        endpoint_id=endpoint.id,
        enrollment_token=token,
        hostname=endpoint.hostname or "",
    )


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(body: HeartbeatRequest, org: Organization = Depends(require_org),
              db=Depends(get_db)):
    """Update last_seen for an endpoint.

    Called by the local backend every 60 seconds. Returns `stale_after_seconds`
    so the local backend knows when to re-enroll if heartbeats are failing.
    """
    endpoint = db.query(Endpoint).filter(
        Endpoint.enrollment_token == body.enrollment_token,
        Endpoint.org_id == org.id,
        Endpoint.is_active.is_(True),
    ).first()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Unknown or inactive endpoint")
    endpoint.last_seen = datetime.utcnow()
    if body.version is not None:
        endpoint.version = body.version
    db.commit()
    return HeartbeatResponse(ok=True, stale_after_seconds=STALE_AFTER_S)


@router.get("", response_model=list[EndpointInfo])
def list_endpoints(
    active_only: bool = Query(False, description="Filter to active endpoints only"),
    org: Organization = Depends(require_org),
    db=Depends(get_db),
):
    """List all endpoints for this organization.

    Used by the dashboard's fleet view. No enrollment tokens are returned.
    """
    q = db.query(Endpoint).filter(Endpoint.org_id == org.id)
    if active_only:
        q = q.filter(Endpoint.is_active.is_(True))
    return q.order_by(Endpoint.last_seen.desc().nullslast()).all()


@router.delete("/{endpoint_id}", status_code=204)
def deactivate_endpoint(endpoint_id: int, org: Organization = Depends(require_org),
                        db=Depends(get_db)):
    """Deactivate an endpoint (soft delete — keeps audit history).

    Deactivation rather than deletion so that audit_events.endpoint_id FK stays valid.
    The endpoint can be re-activated by re-enrolling with the same hostname.
    """
    endpoint = db.query(Endpoint).filter(
        Endpoint.id == endpoint_id, Endpoint.org_id == org.id
    ).first()
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    endpoint.is_active = False
    db.commit()
