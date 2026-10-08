"""Policy API — CRUD for entity-type → action policies. Authenticated and org-scoped.

Endpoints (all require an API key; the organization is the key's organization):
  GET    /api/policies               — list this org's policies
  GET    /api/policies/export        — {entity_type: action} map (for local backend pull)
  GET    /api/policies/{type}        — get policy for a specific entity type
  POST   /api/policies               — create a new policy
  PUT    /api/policies/{id}          — update a policy
  DELETE /api/policies/{id}          — delete a (non-default) policy
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.auth import require_org
from app.db import Organization, Policy, get_db

router = APIRouter(prefix="/api/policies", tags=["policies"])

Action = Literal["faker", "redact", "keep", "block"]
EntityType = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True,
                                              pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]

IMMUTABLE_CRITICAL_SECRETS = frozenset({
    "API_KEY", "AUTH_TOKEN", "JWT", "PRIVATE_KEY", "CLOUD_SECRET",
    "CONNECTION_STRING", "PASSWORD", "RECOVERY_CODE", "CREDIT_CARD", "CVV",
    "US_SSN", "TAX_ID", "MEDICAL_RECORD_NUMBER", "HEALTH_INSURANCE_ID",
    "GOVERNMENT_ID", "PASSPORT_NUMBER", "DRIVERS_LICENSE_NUMBER", "SSN",
})


class PolicyCreate(BaseModel):
    # Unknown fields (e.g. a legacy `org_id`) are ignored: the org always comes from the API key.
    model_config = ConfigDict(extra="ignore")
    entity_type: EntityType
    action: Action


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: Optional[Action] = None
    entity_type: Optional[EntityType] = None


class PolicyResponse(BaseModel):
    id: int
    org_id: int
    entity_type: str
    action: str
    is_default: bool
    version: int


def _own_policy(db, org: Organization, policy_id: int) -> Policy:
    policy = db.query(Policy).filter(Policy.id == policy_id, Policy.org_id == org.id).first()
    if not policy:   # 404 (not 403) for other orgs' ids: don't confirm they exist
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


def _export(db, org: Organization) -> dict[str, str]:
    return {p.entity_type: p.action for p in db.query(Policy).filter(Policy.org_id == org.id).all()}


@router.get("", response_model=list[PolicyResponse])
def list_policies(org: Organization = Depends(require_org), db=Depends(get_db)):
    """List all policies for the caller's organization."""
    return db.query(Policy).filter(Policy.org_id == org.id).all()


# NOTE: /export must be declared BEFORE /{entity_type}, or "export" is captured as an entity type.
@router.get("/export")
def export_policies(org: Organization = Depends(require_org), db=Depends(get_db)):
    """All policies as a routing table: {"PERSON": "faker", "CREDIT_CARD": "redact", ...}."""
    return _export(db, org)


@router.get("/export/all", include_in_schema=False)
def export_policies_legacy(org: Organization = Depends(require_org), db=Depends(get_db)):
    return _export(db, org)


@router.get("/{entity_type}", response_model=PolicyResponse)
def get_policy(entity_type: str, org: Organization = Depends(require_org), db=Depends(get_db)):
    """Get the policy for a specific entity type."""
    policy = db.query(Policy).filter(
        Policy.org_id == org.id,
        Policy.entity_type == entity_type.strip().upper(),
    ).first()
    if not policy:
        raise HTTPException(status_code=404, detail=f"No policy for {entity_type}")
    return policy


@router.post("", response_model=PolicyResponse, status_code=201)
def create_policy(body: PolicyCreate, org: Organization = Depends(require_org), db=Depends(get_db)):
    """Create a new policy."""
    if body.entity_type in IMMUTABLE_CRITICAL_SECRETS and body.action == "keep":
        raise HTTPException(
            status_code=400,
            detail=f"Security violation: critical secret '{body.entity_type}' cannot be configured with Action 'keep'",
        )

    existing = db.query(Policy).filter(
        Policy.org_id == org.id, Policy.entity_type == body.entity_type).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Policy for {body.entity_type} already exists")
    policy = Policy(org_id=org.id, entity_type=body.entity_type, action=body.action)
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


@router.put("/{policy_id}", response_model=PolicyResponse)
def update_policy(policy_id: int, body: PolicyUpdate,
                  org: Organization = Depends(require_org), db=Depends(get_db)):
    """Update a policy."""
    policy = _own_policy(db, org, policy_id)

    target_type = body.entity_type or policy.entity_type
    target_action = body.action or policy.action

    if target_type in IMMUTABLE_CRITICAL_SECRETS and target_action == "keep":
        raise HTTPException(
            status_code=400,
            detail=f"Security violation: critical secret '{target_type}' cannot be configured with Action 'keep'",
        )

    if body.entity_type and body.entity_type != policy.entity_type:
        clash = db.query(Policy).filter(
            Policy.org_id == org.id, Policy.entity_type == body.entity_type, Policy.id != policy.id).first()
        if clash:
            raise HTTPException(status_code=409, detail=f"Policy for {body.entity_type} already exists")
        policy.entity_type = body.entity_type
    if body.action and body.action != policy.action:
        policy.action = body.action
        policy.version += 1

    db.commit()
    db.refresh(policy)
    return policy


@router.delete("/{policy_id}", status_code=204)
def delete_policy(policy_id: int, org: Organization = Depends(require_org), db=Depends(get_db)):
    """Delete a policy."""
    policy = _own_policy(db, org, policy_id)
    if policy.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete default policies")
    db.delete(policy)
    db.commit()
