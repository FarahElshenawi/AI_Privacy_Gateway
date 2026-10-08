"""Routing audit/debug records — kept strictly separate from the vault.

An AuditRecord describes a routing decision for administrators and debugging.
It NEVER contains the original value, any substring of it, a hash of it, or
the replacement placeholder/token. Only the value's length is recorded.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from .policy import RoutingDecision

audit_logger = logging.getLogger("privacy_gateway.audit")


@dataclass(frozen=True)
class AuditRecord:
    entity_type: str
    action: str
    strategy: str
    storage: str
    risk_level: str
    entity_category: str
    rule: str
    timestamp: str
    conversation_id: str
    value_length: int


def build_audit_record(decision: RoutingDecision, conversation_id: str,
                       value_length: int) -> AuditRecord:
    return AuditRecord(
        entity_type=decision.label,
        action=decision.action.name,
        strategy=decision.strategy.value,
        storage=decision.storage.value,
        risk_level=decision.risk_level.value,
        entity_category=decision.entity_category.value,
        rule=decision.rule,
        timestamp=datetime.now(timezone.utc).isoformat(),
        conversation_id=conversation_id,
        value_length=int(value_length),
    )


def emit_audit(record: AuditRecord, logger: Optional[logging.Logger] = None) -> None:
    """Write the record to the audit logger (metadata only)."""
    (logger or audit_logger).info(
        "routing_decision %s",
        " ".join(f"{k}={v}" for k, v in asdict(record).items()),
    )
