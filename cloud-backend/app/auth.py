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

from app.db import Organization, get_db, hash_api_key


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
