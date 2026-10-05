"""Step 2 — Deterministic routing table (0ms).

Entity-type → action mapping. Handles ~90% of entities without the SLM tier.

Routing rules (compliance-driven):
  - redact:  secrets and financial identifiers that can't be faked safely
             (cards, keys, tokens, private keys, bank identifiers)
  - faker:   identity PII where a realistic surrogate is safer than redaction
             (names, emails, phones, usernames — preserves prompt readability)
  - keep:    non-PII infrastructure (public IPs, public URLs)

The routing table is the single source of truth for "what do we do with this
entity type?" Adding a new entity type means adding one line here AND one
faker generator in masking.py (if routed to "faker").
"""

ROUTING_TABLE = {
    # === Identity PII → Faker substitution (preserves prompt readability) ===
    "PERSON": "faker",
    "person": "faker",
    "ORGANIZATION": "faker",
    "organization": "faker",
    "EMAIL": "faker",
    "email": "faker",
    "PHONE_NUMBER": "faker",
    "phone_number": "faker",
    "PHONE_E164": "faker",
    "ADDRESS": "faker",
    "address": "faker",
    "USERNAME": "faker",
    "username": "faker",

    # === Secrets → redaction (can't be faked — checksums would break) ===
    "CREDIT_CARD": "redact",
    "CVV": "redact",
    "CARD_EXPIRY": "redact",
    "API_KEY": "redact",
    "AUTH_TOKEN": "redact",
    "PRIVATE_KEY": "redact",
    "CLOUD_SECRET": "redact",
    "CONNECTION_STRING": "redact",
    "PASSWORD": "redact",
    "RECOVERY_CODE": "redact",
    "JWT": "redact",
    "PEM_BLOCK": "redact",

    # === Financial identifiers → redaction ===
    "IBAN": "redact",
    "SWIFT_BIC": "redact",
    "ABA_ROUTING": "redact",
    "BANK_ACCOUNT_NUMBER": "redact",
    "CRYPTO_WALLET": "redact",

    # === Government / Health IDs → redaction (HIPAA) ===
    "US_SSN": "redact",
    "TAX_ID": "redact",
    "MEDICAL_RECORD_NUMBER": "redact",
    "HEALTH_INSURANCE_ID": "redact",

    # === Non-PII → keep as-is ===
    "IP_ADDRESS": "keep",
    "IPV4": "keep",
    "IPV6": "keep",
    "ip_address": "keep",

    # === Internal infrastructure → redact (leaks corporate network topology) ===
    "INTERNAL_URL": "redact",
    "INTERNAL_HOSTNAME": "redact",

    # === Legacy aliases (backward compat with v1) ===
    "card_number": "redact",
    "secret": "redact",
    "password": "redact",
    "access_token": "redact",
    "SSN": "redact",
    "URL": "keep",
    "url": "keep",
}


def route(entity_type: str) -> str | None:
    """Get the masking action for an entity type.

    Returns: 'faker', 'redact', 'keep', or None (unrouted → falls through to SLM).
    """
    return ROUTING_TABLE.get(entity_type)
