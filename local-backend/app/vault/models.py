"""Vault models — data structures for the mapping vault."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class VaultEntry:
    """A single fake↔real mapping entry."""
    fake_value: str
    real_value: str
    entity_type: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "fake_value": self.fake_value,
            "real_value": self.real_value,
            "entity_type": self.entity_type,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class ConversationVault:
    """All mappings for a single conversation."""
    conversation_id: str
    entries: dict[str, VaultEntry] = field(default_factory=dict)  # fake_value → entry
    _reverse: dict[str, str] = field(default_factory=dict)  # real_value → fake_value

    def add(self, fake_value: str, real_value: str, entity_type: str,
            ttl_hours: float = 24.0) -> VaultEntry:
        """Add a new mapping. Raises CollisionError if fake_value already exists."""
        from app.vault.collision import CollisionError

        if fake_value in self.entries:
            existing = self.entries[fake_value]
            if existing.real_value != real_value:
                raise CollisionError(
                    f"Fake value '{fake_value}' already maps to '{existing.real_value}', "
                    f"cannot remap to '{real_value}'"
                )
            return existing

        entry = VaultEntry(
            fake_value=fake_value,
            real_value=real_value,
            entity_type=entity_type,
            expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
        )
        self.entries[fake_value] = entry
        self._reverse[real_value] = fake_value
        return entry

    def lookup_by_fake(self, fake_value: str) -> Optional[VaultEntry]:
        """Look up the real value for a given fake value."""
        entry = self.entries.get(fake_value)
        if entry is None or entry.is_expired():
            return None
        return entry

    def lookup_by_real(self, real_value: str) -> Optional[VaultEntry]:
        """Look up the fake value for a given real value."""
        fake_value = self._reverse.get(real_value)
        if fake_value is None:
            return None
        return self.lookup_by_fake(fake_value)

    def get_all_fakes(self) -> list[str]:
        """Return all fake values (for restore: find-and-replace in LLM response)."""
        return [e.fake_value for e in self.entries.values() if not e.is_expired()]

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        expired = [k for k, v in self.entries.items() if v.is_expired()]
        for key in expired:
            real = self.entries[key].real_value
            del self.entries[key]
            self._reverse.pop(real, None)
        return len(expired)

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "entries": {k: v.to_dict() for k, v in self.entries.items()},
        }
