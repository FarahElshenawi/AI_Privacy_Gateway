"""Routing audit/debug records — kept strictly separate from the vault.

An AuditRecord describes a routing decision for administrators and debugging.
It NEVER contains the original value, any substring of it, a hash of it, or
the replacement placeholder/token. Only the value's length is recorded.

A DemaskAuditRecord describes a /demask invocation — the most security-sensitive
operation in the system, because it returns real PII. Every demask call must
leave an audit trail so a reviewer can answer "who unmasked what, when, and how
many entities were restored?" without ever having to look at the response body.
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


@dataclass(frozen=True)
class DemaskAuditRecord:
    """Audit record for a /demask call. Contains NO text, NO surrogate, NO real value.

    Fields:
        event_type: always "demask" — distinguishes from routing_decision records.
        conversation_id: which conversation's vault was read.
        replacements_made: how many fakes were restored to real values.
        text_length: length of the input text (the LLM response). Bounded, not the text itself.
        timestamp: ISO-8601 UTC.
    """
    event_type: str = "demask"
    conversation_id: str = ""
    replacements_made: int = 0
    text_length: int = 0
    timestamp: str = ""


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


def build_demask_audit_record(conversation_id: str, replacements_made: int,
                              text_length: int) -> DemaskAuditRecord:
    """Build a demask audit record. Never includes the text or any replaced value."""
    return DemaskAuditRecord(
        event_type="demask",
        conversation_id=conversation_id,
        replacements_made=int(replacements_made),
        text_length=int(text_length),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def emit_audit(record: AuditRecord, logger: Optional[logging.Logger] = None) -> None:
    """Write the routing-decision record to the audit logger (metadata only)."""
    (logger or audit_logger).info(
        "routing_decision %s",
        " ".join(f"{k}={v}" for k, v in asdict(record).items()),
    )


def emit_demask_audit(record: DemaskAuditRecord,
                      logger: Optional[logging.Logger] = None) -> None:
    """Write a demask event to the audit logger (metadata only).

    The log line is parseable as key=value pairs and contains NO real value,
    NO surrogate, NO substring of either the request or response body.
    """
    (logger or audit_logger).info(
        "demask_event %s",
        " ".join(f"{k}={v}" for k, v in asdict(record).items()),
    )
