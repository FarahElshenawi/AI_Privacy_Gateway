"""Admin-only routes: the admin action log and enrollment-key rotation."""
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth import require_org
from app.db import AdminAction, Organization, get_db, hash_api_key, log_admin_action

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminActionInfo(BaseModel):
    id: int
    action: str
    target: str | None
    timestamp: datetime


class NewEnrollKey(BaseModel):
    enroll_key: str


@router.get("/log", response_model=list[AdminActionInfo])
def admin_log(limit: int = Query(100, ge=1, le=1000), org: Organization = Depends(require_org),
              db=Depends(get_db)):
    """What was changed with the admin key (policy edits, audit deletions, device removals)."""
    return (db.query(AdminAction).filter(AdminAction.org_id == org.id)
            .order_by(AdminAction.id.desc()).limit(limit).all())


@router.post("/rotate-enroll-key", response_model=NewEnrollKey)
def rotate_enroll_key(org: Organization = Depends(require_org), db=Depends(get_db)):
    """Issue a new enrollment key (shown once). Existing endpoints keep working: they use their
    own tokens. Use this if the enrollment key leaked."""
    key = secrets.token_urlsafe(32)
    org.enroll_key = hash_api_key(key)
    log_admin_action(db, org, "enroll_key.rotate")
    db.commit()
    return NewEnrollKey(enroll_key=key)
