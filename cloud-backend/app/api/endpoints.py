"""Endpoint enrollment + heartbeat API.

Endpoints are local-backend installations registered to an organization. Each
endpoint has:
  - its own endpoint token (issued once at enrollment; only its hash is stored)
  - a hostname (machine name, for fleet display)
  - a version (local-backend version, for compatibility checks)
  - a last_seen timestamp (updated on every heartbeat)

The local backend enrolls on first startup with the ENROLLMENT key (X-Enroll-Key: it can do
nothing but register devices), then uses its own endpoint token (X-Endpoint-Token) for
heartbeats, audit writes and policy reads. The admin key never has to leave the admin's hands.
Heartbeat every N seconds. The dashboard uses the endpoints table to
show "X devices online, Y devices stale, Z devices running old version".

Credentials: enroll = enrollment key; heartbeat = endpoint token; list/deactivate = admin key.
A leaked endpoint token or enrollment key can't read audit events, edit policies or list devices.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

import secrets

from app.auth import require_endpoint, require_enroll_org, require_org
from app.db import Endpoint, Organization, get_db, hash_api_key, log_admin_action

router = APIRouter(prefix="/api/endpoints", tags=["endpoints"])

Hostname = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,255}$")]
Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._+~-]{1,32}$")]


class EnrollRequest(BaseModel):
    """A local backend enrolls itself on first startup.

    The caller holds the org's ENROLLMENT key (X-Enroll-Key). Enrollment creates an Endpoint
    row and returns that device's own endpoint_token (shown once, only its hash is stored).
    """
    model_config = ConfigDict(extra="forbid")
    hostname: Hostname
    version: Optional[Version] = None


class EnrollResponse(BaseModel):
    endpoint_id: int
    endpoint_token: str
    hostname: str


class HeartbeatRequest(BaseModel):
    """Sent by a local backend every N seconds, authenticated by X-Endpoint-Token.
    The version lets the dashboard track which devices run old local-backend versions."""
    model_config = ConfigDict(extra="forbid")
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
def enroll(body: EnrollRequest, org: Organization = Depends(require_enroll_org),
           db=Depends(get_db)):
    """Register a new local backend installation (called once on first startup)."""
    token = secrets.token_urlsafe(32)
    endpoint = Endpoint(
        org_id=org.id,
        hostname=body.hostname,
        token_hash=hash_api_key(token),     # the token itself is returned once and never stored
        version=body.version,
        last_seen=datetime.utcnow(),
        is_active=True,
    )
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)
    return EnrollResponse(endpoint_id=endpoint.id, endpoint_token=token, hostname=endpoint.hostname or "")


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(body: HeartbeatRequest, endpoint: Endpoint = Depends(require_endpoint),
              db=Depends(get_db)):
    """Update last_seen (require_endpoint already did) and the reported version."""
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
    log_admin_action(db, org, "endpoint.deactivate", f"endpoint:{endpoint_id}")
    db.commit()
