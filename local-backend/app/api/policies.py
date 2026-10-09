"""Policy management API for local gateway.

Allows the admin dashboard or orchestrator to:
- Inspect currently active routing rules (GET /api/policies/active)
- Apply policy overrides from cloud backend / admin dashboard (POST /api/policies/apply)
- Reset policies back to built-in defaults (POST /api/policies/reset)
"""
from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, Header, HTTPException, Request

from dlp_core.policy import Policy, get_active_routing_table, reset_policy_to_defaults
from app.policy_runtime import apply_policies
from app.security.auth import verify_token
from app.security.origin_check import check_origin

router = APIRouter(prefix="/policies", tags=["policies"])


def _require_local_admin(request: Request, authorization: Optional[str]) -> None:
    """Same bar as /api/mask: loopback + extension Origin + Host check, AND the install token.
    These routes change what gets masked, so they must not be callable by a web page or by
    anything that merely reached the port."""
    check_origin(request)
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing token")


@router.get("/active")
def get_active_policies(request: Request, authorization: Optional[str] = Header(None)):
    """Return all active entity routing rules."""
    _require_local_admin(request, authorization)
    table = get_active_routing_table()
    result = {}
    for entity_type, entry in table.items():
        result[entity_type] = {
            "action": entry.action.name.lower(),
            "risk_level": entry.risk_level.value,
            "entity_category": entry.entity_category.value,
            "storage": entry.storage.value,
        }
    return {
        "status": "ok",
        "total": len(result),
        "policies": result,
    }


@router.post("/apply")
def apply_policies_route(payload: dict[str, Any], request: Request, authorization: Optional[str] = Header(None)):
    """Apply policy overrides from the admin dashboard or cloud export.

    Accepts either:
      {"OVERRIDES": {"ORGANIZATION": "redact", ...}}
    or directly:
      {"ORGANIZATION": "redact", "EMAIL": "faker", ...}
    """
    _require_local_admin(request, authorization)
    overrides = payload.get("OVERRIDES", payload)
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="Body must be {LABEL: action} or {\"OVERRIDES\": {...}}")
    flat = {k: (v.get("action") if isinstance(v, dict) else v) for k, v in overrides.items()}
    try:
        applied, rejected = apply_policies(flat, strict=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Rejected (nothing applied): {e}")
    return {
        "status": "ok",
        "applied": applied,
        "rejected": rejected,
        "message": f"Applied {applied} policy overrides to the masking engine.",
    }


@router.post("/reset")
def reset_policies(request: Request, authorization: Optional[str] = Header(None)):
    """Reset routing policies back to default settings."""
    _require_local_admin(request, authorization)
    reset_policy_to_defaults()
    from app.pipeline import engine
    engine._pipeline.set_policy(Policy())
    return {
        "status": "ok",
        "message": "Routing policies have been reset to defaults.",
    }
