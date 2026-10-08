"""Policy management API for local gateway.

Allows the admin dashboard or orchestrator to:
- Inspect currently active routing rules (GET /api/policies/active)
- Apply policy overrides from cloud backend / admin dashboard (POST /api/policies/apply)
- Reset policies back to built-in defaults (POST /api/policies/reset)
"""
from __future__ import annotations

from typing import Any, Mapping
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from dlp_core.policy import (
    PolicyConfigError,
    get_active_routing_table,
    load_policy_config,
    reset_policy_to_defaults,
)
from app.security import origin_check

router = APIRouter(prefix="/policies", tags=["policies"])


def _check_local_admin(request: Request) -> None:
    """Ensure the caller is on the local loopback interface."""
    client_host = request.client.host if request.client else ""
    if client_host and client_host not in origin_check.ALLOWED_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: administration requests from {client_host} are not allowed. "
                   "The backend only accepts localhost connections.",
        )


class PolicyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    OVERRIDES: dict[str, Any] | None = None


@router.get("/active")
def get_active_policies(request: Request):
    """Return all active entity routing rules."""
    _check_local_admin(request)
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
def apply_policies(payload: dict[str, Any], request: Request):
    """Apply policy overrides from the admin dashboard or cloud export.

    Accepts either:
      {"OVERRIDES": {"ORGANIZATION": "redact", ...}}
    or directly:
      {"ORGANIZATION": "redact", "EMAIL": "faker", ...}
    """
    _check_local_admin(request)
    try:
        applied_count = load_policy_config(payload)
        return {
            "status": "ok",
            "applied": applied_count,
            "message": f"Successfully applied {applied_count} policy overrides to routing engine.",
        }
    except PolicyConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to apply policies: {e}")


@router.post("/reset")
def reset_policies(request: Request):
    """Reset routing policies back to default settings."""
    _check_local_admin(request)
    reset_policy_to_defaults()
    return {
        "status": "ok",
        "message": "Routing policies have been reset to defaults.",
    }
