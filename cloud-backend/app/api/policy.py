"""Policy API — CRUD for entity-type → action policies.

Endpoints:
  GET    /api/policies          — list all policies (optionally by org)
  GET    /api/policies/{type}   — get policy for a specific entity type
  POST   /api/policies          — create a new policy
  PUT    /api/policies/{id}     — update a policy
  DELETE /api/policies/{id}     — delete a policy
  GET    /api/policies/export   — export all policies as JSON (for local backend pull)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional

from app.db import get_session, Policy, init_db

router = APIRouter(prefix="/api/policies", tags=["policies"])


class PolicyCreate(BaseModel):
    org_id: int = 1
    entity_type: str
    action: str  # "faker", "redact", "keep"


class PolicyUpdate(BaseModel):
    action: Optional[str] = None
    entity_type: Optional[str] = None


class PolicyResponse(BaseModel):
    id: int
    org_id: int
    entity_type: str
    action: str
    is_default: bool
    version: int


def get_db():
    """Dependency: yields a database session."""
    engine = init_db()
    db = get_session(engine)
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=list[PolicyResponse])
async def list_policies(org_id: int = 1, db=Depends(get_db)):
    """List all policies for an organization."""
    policies = db.query(Policy).filter(Policy.org_id == org_id).all()
    return policies


@router.get("/{entity_type}", response_model=PolicyResponse)
async def get_policy(entity_type: str, org_id: int = 1, db=Depends(get_db)):
    """Get the policy for a specific entity type."""
    policy = db.query(Policy).filter(
        Policy.org_id == org_id,
        Policy.entity_type == entity_type.upper(),
    ).first()
    if not policy:
        raise HTTPException(status_code=404, detail=f"No policy for {entity_type}")
    return policy


@router.post("", response_model=PolicyResponse, status_code=201)
async def create_policy(body: PolicyCreate, db=Depends(get_db)):
    """Create a new policy."""
    existing = db.query(Policy).filter(
        Policy.org_id == body.org_id,
        Policy.entity_type == body.entity_type.upper(),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Policy for {body.entity_type} already exists")
    
    policy = Policy(
        org_id=body.org_id,
        entity_type=body.entity_type.upper(),
        action=body.action.lower(),
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.put("/{policy_id}", response_model=PolicyResponse)
async def update_policy(policy_id: int, body: PolicyUpdate, db=Depends(get_db)):
    """Update a policy."""
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    if body.action:
        policy.action = body.action.lower()
        policy.version += 1
    if body.entity_type:
        policy.entity_type = body.entity_type.upper()

    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(policy_id: int, db=Depends(get_db)):
    """Delete a policy."""
    policy = db.query(Policy).filter(Policy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    if policy.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete default policies")
    db.delete(policy)
    db.commit()


@router.get("/export/all")
async def export_policies(org_id: int = 1, db=Depends(get_db)):
    """Export all policies as a routing table (for local backend pull).

    Returns a simple JSON dict: {"PERSON": "faker", "CREDIT_CARD": "redact", ...}
    """
    policies = db.query(Policy).filter(Policy.org_id == org_id).all()
    return {p.entity_type: p.action for p in policies}
