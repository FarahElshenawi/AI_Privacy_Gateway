"""Canonical default policies catalog for the Cloud Control Plane.

Single source of truth matching local-backend/dlp_core/policy.py ROUTING_TABLE.
Contains all 39 canonical configurable entities and their default actions.

Rules:
- Aliases (SSN, SECRET, ACCESS_TOKEN, CARD_NUMBER) are excluded (handled by normalization).
- Value-dependent IP labels (IP_ADDRESS, IPV4, IPV6) are excluded (evaluated dynamically).
"""
from __future__ import annotations

CANONICAL_DEFAULT_POLICIES: dict[str, str] = {
    # Identity PII -> faker
    "PERSON": "faker",
    "ORGANIZATION": "faker",
    "LOCATION": "faker",
    "EMAIL": "faker",
    "PHONE_NUMBER": "faker",
    "PHONE_E164": "faker",
    "ADDRESS": "faker",
    "USERNAME": "faker",
    "DATE_OF_BIRTH": "faker",

    # Payment & Financial -> redact
    "CREDIT_CARD": "redact",
    "CVV": "redact",
    "CARD_EXPIRY": "redact",
    "IBAN": "redact",
    "SWIFT_BIC": "redact",
    "ABA_ROUTING": "redact",
    "BANK_ACCOUNT_NUMBER": "redact",
    "CRYPTO_WALLET": "redact",

    # Government & Health IDs -> redact
    "US_SSN": "redact",
    "TAX_ID": "redact",
    "MEDICAL_RECORD_NUMBER": "redact",
    "HEALTH_INSURANCE_ID": "redact",
    "GOVERNMENT_ID": "redact",
    "PASSPORT_NUMBER": "redact",
    "DRIVERS_LICENSE_NUMBER": "redact",
    "ACCOUNT_ID": "redact",
    "SENSITIVE_DATE": "redact",
    "DENY_TERM": "redact",

    # Credentials & Secrets -> redact
    "API_KEY": "redact",
    "AUTH_TOKEN": "redact",
    "JWT": "redact",
    "PRIVATE_KEY": "redact",
    "PEM_BLOCK": "redact",
    "CLOUD_SECRET": "redact",
    "CONNECTION_STRING": "redact",
    "PASSWORD": "redact",
    "RECOVERY_CODE": "redact",

    # Infrastructure -> redact / keep
    "INTERNAL_URL": "redact",
    "INTERNAL_HOSTNAME": "redact",
    "URL": "keep",
}
