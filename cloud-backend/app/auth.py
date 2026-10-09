"""API-key authentication for the control plane.

Every /api/* route depends on `require_org`. The caller's organization comes from the key
(NOT from a client-supplied org_id), so one organization can never read or change another's
policies or audit events, and an unauthenticated caller can read/change nothing.

Send the key as `X-API-Key: <key>` or `Authorization: Bearer <key>`.
Only a SHA-256 digest of each key is stored (see db.hash_api_key); lookup is by that digest,
so no timing signal about the stored value is exposed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from datetime import datetime

from app.db import Endpoint, Organization, get_db, hash_api_key


def _extract_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
        return key or None
    return None


def require_org(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
    db=Depends(get_db),
) -> Organization:
    key = _extract_key(x_api_key, authorization)
    if key is None:
        raise HTTPException(status_code=401, detail="Missing API key (X-API-Key header)",
                            headers={"WWW-Authenticate": "Bearer"})
    org = (
        db.query(Organization)
        .filter(Organization.api_key == hash_api_key(key), Organization.is_active.is_(True))
        .first()
    )
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid API key",
                            headers={"WWW-Authenticate": "Bearer"})
    return org


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def require_enroll_org(
    x_enroll_key: Optional[str] = Header(None, alias="X-Enroll-Key"),
    db=Depends(get_db),
) -> Organization:
    """ENROLLMENT key: can only register a new endpoint. Cannot read policies or audit events."""
    if not x_enroll_key or not x_enroll_key.strip():
        raise _unauthorized("Missing enrollment key (X-Enroll-Key header)")
    org = (db.query(Organization)
           .filter(Organization.enroll_key == hash_api_key(x_enroll_key.strip()),
                   Organization.is_active.is_(True)).first())
    if org is None:
        raise _unauthorized("Invalid enrollment key")
    return org


def require_endpoint(
    x_endpoint_token: Optional[str] = Header(None, alias="X-Endpoint-Token"),
    db=Depends(get_db),
) -> Endpoint:
    """ENDPOINT token (one per device): heartbeat, write audit events, read the policy snapshot."""
    if not x_endpoint_token or not x_endpoint_token.strip():
        raise _unauthorized("Missing endpoint token (X-Endpoint-Token header)")
    ep = (db.query(Endpoint)
          .filter(Endpoint.token_hash == hash_api_key(x_endpoint_token.strip()),
                  Endpoint.is_active.is_(True)).first())
    if ep is None or not ep.organization.is_active:
        raise _unauthorized("Invalid endpoint token")
    ep.last_seen = datetime.utcnow()
    db.commit()
    return ep


class Caller:
    """Who is calling an endpoint that both admins and devices may use."""
    def __init__(self, org: Organization, endpoint: Optional[Endpoint] = None):
        self.org, self.endpoint = org, endpoint


def require_org_or_endpoint(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
    x_endpoint_token: Optional[str] = Header(None, alias="X-Endpoint-Token"),
    db=Depends(get_db),
) -> Caller:
    """Admin key OR endpoint token. Used ONLY by the routes a device legitimately needs
    (write audit events, read the policy snapshot)."""
    if x_endpoint_token and x_endpoint_token.strip():
        ep = require_endpoint(x_endpoint_token, db)
        return Caller(ep.organization, ep)
    org = require_org(x_api_key, authorization, db)
    return Caller(org)
