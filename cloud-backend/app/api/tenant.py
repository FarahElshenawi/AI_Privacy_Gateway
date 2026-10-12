"""Tenant detection config: customer-specific deny terms and internal domains.

  GET /api/tenant-config   admin key or a device token (devices pull this with their policies)
  PUT /api/tenant-config   admin key only; replaces both lists

Deny terms can themselves be sensitive (project and client names), so they are never written to the
admin log or to server logs: the log records only how many terms/domains were set.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import Caller, require_org, require_org_or_endpoint
from app.db import Organization, TenantConfig, get_db, log_admin_action

router = APIRouter(prefix="/api/tenant-config", tags=["tenant"])

MAX_TERMS, MAX_DOMAINS = 500, 200
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class TenantConfigBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    deny_terms: list[str] = Field(default_factory=list, max_length=MAX_TERMS)
    tenant_domains: list[str] = Field(default_factory=list, max_length=MAX_DOMAINS)
    # default: images inside Word/Excel/PDF are allowed with a warning, standalone images are blocked.
    # block: any image blocks the file. warn: images are allowed and sent UNCHANGED, with a warning.
    image_policy: Literal["default", "block", "warn"] = "default"

    @field_validator("deny_terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        out, seen = [], set()
        for t in v:
            t = t.strip()
            if not 2 <= len(t) <= 100 or _CTRL.search(t):
                raise ValueError("each deny term must be 2-100 printable characters")
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    @field_validator("tenant_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out = []
        for d in v:
            d = d.strip().lower().removeprefix("*.").lstrip(".")
            if len(d) > 253 or not _HOST.match(d):
                raise ValueError("tenant domains must be hostnames such as corp.acme.com")
            if d not in out:
                out.append(d)
        return out


class TenantConfigResponse(TenantConfigBody):
    version: int


def _load(db, org: Organization) -> TenantConfigResponse:
    row = db.get(TenantConfig, org.id)
    if row is None:
        return TenantConfigResponse(version=0)
    return TenantConfigResponse(deny_terms=json.loads(row.deny_terms),
                                tenant_domains=json.loads(row.tenant_domains), image_policy=row.image_policy,
                                version=row.version)


@router.get("", response_model=TenantConfigResponse)
def get_tenant_config(caller: Caller = Depends(require_org_or_endpoint), db=Depends(get_db)):
    return _load(db, caller.org)


@router.put("", response_model=TenantConfigResponse)
def put_tenant_config(body: TenantConfigBody, org: Organization = Depends(require_org), db=Depends(get_db)):
    row = db.get(TenantConfig, org.id)
    if row is None:
        row = TenantConfig(org_id=org.id, version=1)
        db.add(row)
    else:
        row.version += 1
    row.deny_terms = json.dumps(body.deny_terms)
    row.tenant_domains = json.dumps(body.tenant_domains)
    row.image_policy = body.image_policy
    log_admin_action(db, org, "tenant_config.update",
                     f"{len(body.deny_terms)} terms, {len(body.tenant_domains)} domains, images={body.image_policy}")
    db.commit()
    return _load(db, org)
