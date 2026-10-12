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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StringConstraints, field_serializer

from app.auth import Caller, require_org, require_org_or_endpoint
from app.db import Organization, Policy, PolicyChange, get_db, log_admin_action, log_policy_change

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


class PolicyChangeInfo(BaseModel):
    id: int
    entity_type: str
    old_action: Optional[str]
    new_action: Optional[str]
    changed_at: datetime

    @field_serializer("changed_at")
    def _utc(self, v: datetime) -> str:
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat().replace("+00:00", "Z")


@router.get("/history", response_model=list[PolicyChangeInfo])
def policy_history(limit: int = Query(100, ge=1, le=1000), org: Organization = Depends(require_org),
                   db=Depends(get_db)):
    """Policy change log: what each entity type was changed from and to, newest first."""
    return (db.query(PolicyChange).filter(PolicyChange.org_id == org.id)
            .order_by(PolicyChange.id.desc()).limit(limit).all())


# NOTE: /export and /history must be declared BEFORE /{entity_type}, or "export" is captured as an entity type.
@router.get("/export")
def export_policies(caller: Caller = Depends(require_org_or_endpoint), db=Depends(get_db)):
    """All policies as a routing table: {"PERSON": "faker", "CREDIT_CARD": "redact", ...}.
    Devices read this with their endpoint token (the only policy route they can use)."""
    return _export(db, caller.org)


@router.get("/export/all", include_in_schema=False)
def export_policies_legacy(caller: Caller = Depends(require_org_or_endpoint), db=Depends(get_db)):
    return _export(db, caller.org)


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
    log_policy_change(db, org, body.entity_type, None, body.action)
    log_admin_action(db, org, "policy.create", f"{body.entity_type}={body.action}")
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

    old_type, old_action = policy.entity_type, policy.action
    if body.entity_type and body.entity_type != policy.entity_type:
        clash = db.query(Policy).filter(
            Policy.org_id == org.id, Policy.entity_type == body.entity_type, Policy.id != policy.id).first()
        if clash:
            raise HTTPException(status_code=409, detail=f"Policy for {body.entity_type} already exists")
        policy.entity_type = body.entity_type
    if body.action and body.action != policy.action:
        policy.action = body.action
        policy.version += 1

    if (policy.entity_type, policy.action) != (old_type, old_action):
        if policy.entity_type != old_type:           # renamed: the old type loses its policy, the new one gains it
            log_policy_change(db, org, old_type, old_action, None)
            log_policy_change(db, org, policy.entity_type, None, policy.action)
        else:
            log_policy_change(db, org, policy.entity_type, old_action, policy.action)
    log_admin_action(db, org, "policy.update", f"policy:{policy.id} {policy.entity_type}={policy.action}")
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
    log_policy_change(db, org, policy.entity_type, policy.action, None)
    log_admin_action(db, org, "policy.delete", f"policy:{policy_id} {policy.entity_type}")
    db.commit()
