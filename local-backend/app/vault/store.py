"""Vault CRUD. Owner: Role 2. Never accessed from the extension's page
context — Local Backend only (docs/architecture.md 1.4)."""

def put(conversation_id: str, entity_type: str, real_value: str, fake_value: str) -> None:
    # TODO: insert, enforcing bijective constraint (see collision.py)
    raise NotImplementedError

def get_real(conversation_id: str, fake_value: str) -> str | None:
    # TODO: lookup for demasking
    raise NotImplementedError

def expire_stale() -> int:
    # TODO: delete entries past expires_at (24h), return count deleted
    raise NotImplementedError
