"""Step 2 — Deterministic routing table (0ms).

Entity-type -> action mapping. Handles ~90% of entities without the SLM tier.
"""

ROUTING_TABLE = {
    # Identity PII → Faker substitution
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

    # Secrets → redaction
    "CREDIT_CARD": "redact",
    "card_number": "redact",
    "API_KEY": "redact",
    "secret": "redact",
    "password": "redact",
    "access_token": "redact",
    "JWT": "redact",
    "PEM_BLOCK": "redact",
    "IBAN": "redact",
    "SSN": "redact",

    # Non-PII → keep as-is
    "URL": "keep",
    "IPV4": "keep",
    "IPV6": "keep",
    "ip_address": "keep",
    "url": "keep",
}


def route(entity_type: str) -> str | None:
    """Get the masking action for an entity type.

    Returns: 'faker', 'redact', 'keep', or None (unrouted → falls through to SLM).
    """
    return ROUTING_TABLE.get(entity_type)
