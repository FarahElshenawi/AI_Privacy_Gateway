"""Policy: what to DO with a label. Detection never decides this.

This module provides two integrated layers:
1. New-architecture layer (used by MergeEngine and OffsetMasker):
   `Action`, `Policy`, `DEFAULT_ACTIONS`.
2. Role 4 deterministic routing layer:
   `StoragePolicy`, `RiskLevel`, `EntityCategory`, `ProcessingStrategy`,
   `RoutingEntry`, `RoutingDecision`, `ROUTING_TABLE`, `ALIASES`,
   `is_publicly_routable_ip()`, `route_label()`, `get_routing_entry()`.

Routing is fully deterministic: no ML/LLM/SLM, no network calls.
Unknown types fail-closed to REDACT + NO_STORE.
The only value-dependent decision is IP address classification, performed
offline using the stdlib `ipaddress` module (IANA registries, RFC 6890/8190).
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Mapping, Optional


# ─────────────────────────────────────────────────────────────
# ACTION & NEW ARCHITECTURE LAYER
# ─────────────────────────────────────────────────────────────

class Action(IntEnum):
    """Ordered by strictness: max() of actions is the strictest."""
    KEEP = 0     # leave in clear (explicitly non-sensitive)
    FAKER = 1    # reversible surrogate, preserves prompt readability
    REDACT = 2   # irreversible placeholder, nothing stored (or stored if policy specifies)
    BLOCK = 3    # refuse the request entirely (stops processing immediately)


# Compatibility alias with Role 4 terminology
PolicyAction = Action

_F, _R, _B, _K = Action.FAKER, Action.REDACT, Action.BLOCK, Action.KEEP


# ─────────────────────────────────────────────────────────────
# ROLE 4 ENUMS & MODELS
# ─────────────────────────────────────────────────────────────

class StoragePolicy(str, Enum):
    """WHETHER the original value is retained (independent of action/strategy).

    STORE_FOR_DEMASKING: original <-> surrogate/placeholder kept in vault.
    NO_STORE: original never enters the vault. Secure default.
    """
    STORE_FOR_DEMASKING = "STORE_FOR_DEMASKING"
    NO_STORE = "NO_STORE"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class EntityCategory(str, Enum):
    PII = "PII"
    FINANCIAL = "FINANCIAL"
    CREDENTIAL = "CREDENTIAL"
    GOV_ID = "GOV_ID"
    HEALTH = "HEALTH"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    UNKNOWN = "UNKNOWN"


class ProcessingStrategy(str, Enum):
    """HOW the value is transformed."""
    PSEUDONYMIZE = "PSEUDONYMIZE"   # FAKER
    REDACTION = "REDACTION"         # REDACT
    NONE = "NONE"                   # KEEP
    REJECTION = "REJECTION"         # BLOCK


_STRATEGY_FOR_ACTION: dict[Action, ProcessingStrategy] = {
    Action.FAKER: ProcessingStrategy.PSEUDONYMIZE,
    Action.REDACT: ProcessingStrategy.REDACTION,
    Action.KEEP: ProcessingStrategy.NONE,
    Action.BLOCK: ProcessingStrategy.REJECTION,
}


@dataclass(frozen=True)
class RoutingEntry:
    """Policy metadata for an entity type."""
    action: Action
    risk_level: RiskLevel
    entity_category: EntityCategory
    storage: StoragePolicy = StoragePolicy.NO_STORE

    @property
    def strategy(self) -> ProcessingStrategy:
        return _STRATEGY_FOR_ACTION[self.action]


# Compatibility alias
PolicyEntry = RoutingEntry


@dataclass(frozen=True)
class RoutingDecision:
    """The routing decision for a detected entity."""
    label: str                     # canonical (normalized) entity type
    action: Action
    strategy: ProcessingStrategy
    risk_level: RiskLevel
    entity_category: EntityCategory
    storage: StoragePolicy
    rule: str                      # audit trail

    @property
    def entity_type(self) -> str:
        return self.label


# ── Metadata shortcuts ─────────────────────────────────────────
_LOW, _MED, _HIGH, _CRIT = RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL
_PII, _FIN, _CRED = EntityCategory.PII, EntityCategory.FINANCIAL, EntityCategory.CREDENTIAL
_GOV, _HLTH, _INFRA = EntityCategory.GOV_ID, EntityCategory.HEALTH, EntityCategory.INFRASTRUCTURE
_STORE = StoragePolicy.STORE_FOR_DEMASKING

# Canonical (UPPERCASE) table — single source of truth
ROUTING_TABLE: Mapping[str, RoutingEntry] = {
    # Identity PII -> FAKER (realistic surrogate preserves prompt readability)
    "PERSON": RoutingEntry(_F, _HIGH, _PII, _STORE),
    "ORGANIZATION": RoutingEntry(_F, _MED, _PII, _STORE),
    "LOCATION": RoutingEntry(_F, _MED, _PII, _STORE),
    "EMAIL": RoutingEntry(_F, _HIGH, _PII, _STORE),
    "PHONE_NUMBER": RoutingEntry(_F, _HIGH, _PII, _STORE),
    "PHONE_E164": RoutingEntry(_F, _HIGH, _PII, _STORE),
    "ADDRESS": RoutingEntry(_F, _HIGH, _PII, _STORE),
    "USERNAME": RoutingEntry(_F, _HIGH, _PII, _STORE),
    "DATE_OF_BIRTH": RoutingEntry(_F, _HIGH, _PII, _STORE),

    # Payment cards -> REDACT (surrogate would break checksums)
    "CREDIT_CARD": RoutingEntry(_R, _CRIT, _FIN),
    "CVV": RoutingEntry(_R, _CRIT, _FIN),
    "CARD_EXPIRY": RoutingEntry(_R, _HIGH, _FIN),

    # Bank / crypto identifiers -> REDACT + STORE_FOR_DEMASKING
    "IBAN": RoutingEntry(_R, _HIGH, _FIN, _STORE),
    "SWIFT_BIC": RoutingEntry(_R, _MED, _FIN, _STORE),
    "ABA_ROUTING": RoutingEntry(_R, _HIGH, _FIN, _STORE),
    "BANK_ACCOUNT_NUMBER": RoutingEntry(_R, _HIGH, _FIN),
    "CRYPTO_WALLET": RoutingEntry(_R, _HIGH, _FIN, _STORE),

    # Government / health identifiers -> REDACT
    "US_SSN": RoutingEntry(_R, _CRIT, _GOV),
    "TAX_ID": RoutingEntry(_R, _CRIT, _GOV),
    "MEDICAL_RECORD_NUMBER": RoutingEntry(_R, _CRIT, _HLTH),
    "HEALTH_INSURANCE_ID": RoutingEntry(_R, _CRIT, _HLTH),
    "GOVERNMENT_ID": RoutingEntry(_R, _CRIT, _GOV),
    "PASSPORT_NUMBER": RoutingEntry(_R, _CRIT, _GOV),
    "DRIVERS_LICENSE_NUMBER": RoutingEntry(_R, _CRIT, _GOV),
    "ACCOUNT_ID": RoutingEntry(_R, _HIGH, _GOV),
    "SENSITIVE_DATE": RoutingEntry(_R, _MED, _PII),
    "DENY_TERM": RoutingEntry(_R, _HIGH, _CRED),

    # Secrets & credentials -> REDACT + NO_STORE
    "API_KEY": RoutingEntry(_R, _CRIT, _CRED),
    "AUTH_TOKEN": RoutingEntry(_R, _CRIT, _CRED),
    "JWT": RoutingEntry(_R, _CRIT, _CRED),
    "PRIVATE_KEY": RoutingEntry(_R, _CRIT, _CRED),
    "PEM_BLOCK": RoutingEntry(_R, _HIGH, _CRED),
    "CLOUD_SECRET": RoutingEntry(_R, _CRIT, _CRED),
    "CONNECTION_STRING": RoutingEntry(_R, _CRIT, _CRED),
    "PASSWORD": RoutingEntry(_R, _CRIT, _CRED),
    "RECOVERY_CODE": RoutingEntry(_R, _CRIT, _CRED),

    # Infrastructure
    "INTERNAL_URL": RoutingEntry(_R, _HIGH, _INFRA, _STORE),
    "INTERNAL_HOSTNAME": RoutingEntry(_R, _HIGH, _INFRA, _STORE),
    "URL": RoutingEntry(_K, _LOW, _INFRA),
}

# Legacy / alternate spellings
ALIASES: Mapping[str, str] = {
    "SSN": "US_SSN",
    "CARD_NUMBER": "CREDIT_CARD",
    "SECRET": "CLOUD_SECRET",
    "ACCESS_TOKEN": "AUTH_TOKEN",
}

# Types whose policy depends on actual text value (IP classification)
_IP_LABELS = frozenset({"IP_ADDRESS", "IPV4", "IPV6"})
_IP_KEEP = RoutingEntry(_K, _LOW, _INFRA)
_IP_REDACT = RoutingEntry(_R, _HIGH, _INFRA)
_UNKNOWN = RoutingEntry(_R, RiskLevel.UNKNOWN, EntityCategory.UNKNOWN)

_NAT64 = ipaddress.ip_network("64:ff9b::/96")

# Export for new architecture MergeEngine compatibility
DEFAULT_ACTIONS: Mapping[str, Action] = {
    **{label: entry.action for label, entry in ROUTING_TABLE.items()},
    **{alias: ROUTING_TABLE[canon].action for alias, canon in ALIASES.items() if canon in ROUTING_TABLE},
    "SECRET": Action.REDACT,
}


# ─────────────────────────────────────────────────────────────
# NEW ARCHITECTURE POLICY CLASS
# ─────────────────────────────────────────────────────────────

class Policy:
    """Label -> Action table. Unknown labels get `default` (REDACT = fail closed)."""

    def __init__(self, table: Optional[Mapping[str, Action]] = None,
                 default: Action = Action.REDACT) -> None:
        if default is Action.KEEP:
            raise ValueError("default action must not be KEEP (would fail open)")
        base = dict(DEFAULT_ACTIONS if table is None else table)
        self._table = {k.strip().upper(): Action(v) for k, v in base.items()}
        self._default = Action(default)

    def action_for(self, label: str, value: Optional[str] = None) -> Action:
        norm = normalize_label(label)
        if norm in _IP_LABELS and value is not None:
            if is_publicly_routable_ip(value):
                return Action.KEEP
            return Action.REDACT
        return self._table.get(norm, self._default)

    def with_overrides(self, **overrides: Action) -> "Policy":
        merged = dict(self._table)
        merged.update({normalize_label(k): v for k, v in overrides.items()})
        return Policy(merged, self._default)


# ─────────────────────────────────────────────────────────────
# DETERMINISTIC ROUTING FUNCTIONS
# ─────────────────────────────────────────────────────────────

def normalize_label(label: Optional[str]) -> str:
    """Canonicalize an entity label: trim, uppercase, resolve legacy aliases."""
    key = (label or "").strip().upper()
    return ALIASES.get(key, key)


normalize_type = normalize_label  # Alias for Role 4 naming consistency


def is_publicly_routable_ip(value: Optional[str]) -> bool:
    """True ONLY if value is provably a globally reachable IP address.

    Deterministic, offline. Uses stdlib ipaddress (RFC 6890 / 8190).
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    s = s.split("%", 1)[0]  # drop zone id
    if not s:
        return False
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return False

    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.sixtofour is not None:
            ip = ip.sixtofour

    return bool(ip.is_global) and not ip.is_multicast


def _make_decision(canonical: str, entry: RoutingEntry, rule: str) -> RoutingDecision:
    return RoutingDecision(
        label=canonical,
        action=entry.action,
        strategy=entry.strategy,
        risk_level=entry.risk_level,
        entity_category=entry.entity_category,
        storage=entry.storage,
        rule=rule,
    )


def route_label(label: str, value: Optional[str] = None) -> RoutingDecision:
    """Return the deterministic routing decision for a label/value pair."""
    canonical = normalize_label(label)

    if canonical in _IP_LABELS:
        if is_publicly_routable_ip(value):
            return _make_decision(canonical, _IP_KEEP, "ip:globally-reachable")
        return _make_decision(canonical, _IP_REDACT, "ip:non-global-or-unparseable")

    entry = ROUTING_TABLE.get(canonical)
    if entry is None:
        return _make_decision(canonical, _UNKNOWN, "default:unknown-type")
    return _make_decision(canonical, entry, f"policy:{canonical}")


def route_entity(entity: Mapping) -> RoutingDecision:
    """Decide policy for entity dict with 'type' (or 'label') and 'text'."""
    raw_type = entity.get("type") or entity.get("label", "")
    text = entity.get("text")
    return route_label(raw_type, text)


def route_entities(entities: Iterable[Mapping]) -> list[RoutingDecision]:
    """Route a batch of entities in original order."""
    return [route_entity(e) for e in entities]


def get_routing_entry(label: Optional[str]) -> RoutingEntry:
    """Type-only lookup. IP types resolve to safe REDACT."""
    canonical = normalize_label(label)
    if canonical in _IP_LABELS:
        return _IP_REDACT
    return ROUTING_TABLE.get(canonical, _UNKNOWN)


get_policy = get_routing_entry  # Role 4 alias


def should_block(entities: Iterable[Mapping]) -> bool:
    """True if any entity's decision is BLOCK."""
    return any(d.action is Action.BLOCK for d in route_entities(entities))
