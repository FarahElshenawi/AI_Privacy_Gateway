"""Mapping Vault schema. Owner: Role 2.

conversation_id, fake_value, real_value, entity_type, created_at, expires_at.
Bijective: no fake_value maps to two real_values within a conversation.
Scope: per-conversation, keyed by the ChatGPT conversation URL. Lifetime: 24h.
See docs/architecture.md 1.3.
"""
from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class VaultEntry(Base):
    __tablename__ = "vault_entries"
    id = Column(String, primary_key=True)
    conversation_id = Column(String, index=True, nullable=False)
    entity_type = Column(String, nullable=False)
    fake_value = Column(String, nullable=False)
    real_value = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
