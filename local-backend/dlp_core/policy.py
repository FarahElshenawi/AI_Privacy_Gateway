"""Policy: what to DO with a label. Detection never decides this."""
from __future__ import annotations

from enum import IntEnum
from typing import Mapping, Optional


class Action(IntEnum):
    """Ordered by strictness: max() of actions is the strictest."""
    KEEP = 0     # leave in clear (explicitly non-sensitive)
    FAKER = 1    # reversible surrogate, preserves prompt readability
    REDACT = 2   # irreversible placeholder, nothing stored


_F, _R = Action.FAKER, Action.REDACT

DEFAULT_ACTIONS: Mapping[str, Action] = {
    # identity -> surrogate
    "PERSON": _F, "ORGANIZATION": _F, "EMAIL": _F, "PHONE_NUMBER": _F,
    "ADDRESS": _F, "USERNAME": _F, "LOCATION": _F, "DATE_OF_BIRTH": _F,
    # payment / banking / crypto
    "CREDIT_CARD": _R, "CVV": _R, "CARD_EXPIRY": _R, "IBAN": _R, "SWIFT_BIC": _R,
    "ABA_ROUTING": _R, "BANK_ACCOUNT_NUMBER": _R, "CRYPTO_WALLET": _R,
    # government / health
    "US_SSN": _R, "TAX_ID": _R, "MEDICAL_RECORD_NUMBER": _R, "HEALTH_INSURANCE_ID": _R,
    # secrets
    "API_KEY": _R, "AUTH_TOKEN": _R, "PRIVATE_KEY": _R, "CLOUD_SECRET": _R,
    "CONNECTION_STRING": _R, "PASSWORD": _R, "RECOVERY_CODE": _R,
    # infrastructure
    "IP_ADDRESS": _R, "INTERNAL_URL": _R, "INTERNAL_HOSTNAME": _R,
    # Tier 2 / tenant labels: explicit so behaviour never depends on the fail-closed default.
    # SENSITIVE_DATE is a PRODUCT decision (redacting every date hurts prompts that discuss
    # schedules); change it with Policy().with_overrides(SENSITIVE_DATE=Action.KEEP).
    "GOVERNMENT_ID": _R, "PASSPORT_NUMBER": _R, "DRIVERS_LICENSE_NUMBER": _R, "SECRET": _R,
    "ACCOUNT_ID": _R, "SENSITIVE_DATE": _R, "DENY_TERM": _R,
}


class Policy:
    """Label -> Action table. Unknown labels get `default` (REDACT = fail closed)."""

    def __init__(self, table: Optional[Mapping[str, Action]] = None,
                 default: Action = Action.REDACT) -> None:
        if default is Action.KEEP:
            raise ValueError("default action must not be KEEP (would fail open)")
        base = dict(DEFAULT_ACTIONS if table is None else table)
        self._table = {k.strip().upper(): Action(v) for k, v in base.items()}
        self._default = Action(default)

    def action_for(self, label: str) -> Action:
        return self._table.get(label, self._default)

    def with_overrides(self, **overrides: Action) -> "Policy":
        merged = dict(self._table)
        merged.update({k.upper(): v for k, v in overrides.items()})
        return Policy(merged, self._default)
