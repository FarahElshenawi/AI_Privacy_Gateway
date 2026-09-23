"""Step 2 — Deterministic routing table (0ms). Owner: Role 4.

Entity-type -> action mapping. See docs/architecture.md 1.2 for the v1 table
(PERSON/EMAIL/PHONE -> faker, CREDIT_CARD/API_KEY -> redact, ORGANIZATION ->
decision-routed, URL -> keep). Handles ~90% of entities without the SLM tier.
"""

ROUTING_TABLE = {
    "PERSON": "faker",
    "EMAIL": "faker",
    "PHONE_NUMBER": "faker",
    "CREDIT_CARD": "redact",
    "API_KEY": "redact",
    "URL": "keep",
    # ORGANIZATION intentionally omitted -> falls through to decision.py
}


def route(entity_type: str) -> str | None:
    return ROUTING_TABLE.get(entity_type)
