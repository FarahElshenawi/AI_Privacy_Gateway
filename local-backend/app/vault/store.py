"""Mapping vault — per-conversation, bijective, with TTL.

The vault stores fake↔real mappings keyed by conversation ID.
Within a conversation, each fake value maps to exactly one real value (bijective).
Cross-conversation, the same real value gets different fakes (no fingerprint).

The vault is in-memory by default (with optional on-disk persistence).
Entries expire after a configurable TTL (default: 24 hours).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.vault.models import ConversationVault, VaultEntry
from app.vault.collision import CollisionError


class VaultStore:
    """In-memory vault store with optional on-disk persistence.

    Usage:
        vault = VaultStore()
        vault.add_mapping("conv_123", "James Walsh", "Farah Ahmed", "PERSON")
        entry = vault.lookup_by_fake("conv_123", "James Walsh")
        # entry.real_value == "Farah Ahmed"
    """

    def __init__(self, persistence_path: Optional[str] = None, default_ttl_hours: float = 24.0,
                 sqlite_path: Optional[str] = None):
        """
        Args:
            persistence_path: if set, vault state is loaded/saved to this JSON file.
            default_ttl_hours: how long entries live before expiring.
            sqlite_path: if set, vault state is stored in this SQLite database.
        """
        self._conversations: dict[str, ConversationVault] = {}
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._sqlite_path = Path(sqlite_path) if sqlite_path else None
        self._default_ttl = default_ttl_hours

        if self._sqlite_path:
            self._load_from_sqlite()
        elif self._persistence_path and self._persistence_path.exists():
            self._load_from_disk()

    def add_mapping(
        self,
        conversation_id: str,
        fake_value: str,
        real_value: str,
        entity_type: str,
        ttl_hours: Optional[float] = None,
    ) -> VaultEntry:
        """Add a fake→real mapping for a conversation.

        Raises CollisionError if the fake value already maps to a different real.
        """
        conv = self._get_or_create_conversation(conversation_id)
        entry = conv.add(
            fake_value=fake_value,
            real_value=real_value,
            entity_type=entity_type,
            ttl_hours=ttl_hours or self._default_ttl,
        )
        self._persist()
        return entry

    def _persist(self) -> None:
        if self._sqlite_path is not None:
            self._save_to_sqlite()
        else:
            self._save_to_disk()

    def lookup_by_fake(self, conversation_id: str, fake_value: str) -> Optional[VaultEntry]:
        """Look up the real value for a fake value in a conversation."""
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        return conv.lookup_by_fake(fake_value)

    def lookup_by_real(self, conversation_id: str, real_value: str) -> Optional[VaultEntry]:
        """Look up the fake value for a real value in a conversation."""
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        return conv.lookup_by_real(real_value)

    def restore_text(self, conversation_id: str, text: str) -> str:
        """Replace all fake values in text with their real values.

        This is used to restore the LLM's response: search for fake values
        and replace them with the real ones from the vault.
        """
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return text

        result = text
        # Sort by length descending so longer fakes are replaced first
        # (prevents partial replacements like "James" matching inside "James Walsh")
        fakes = sorted(conv.get_all_fakes(), key=len, reverse=True)
        for fake_value in fakes:
            entry = conv.lookup_by_fake(fake_value)
            if entry:
                result = result.replace(fake_value, entry.real_value)
        return result

    def cleanup_expired(self) -> int:
        """Remove all expired entries across all conversations."""
        total_removed = 0
        for conv in self._conversations.values():
            total_removed += conv.cleanup_expired()
        if total_removed > 0:
            self._persist()
        return total_removed

    def get_conversation(self, conversation_id: str) -> Optional[ConversationVault]:
        """Get the full vault for a conversation (for debugging/inspection)."""
        return self._conversations.get(conversation_id)

    def clear_conversation(self, conversation_id: str) -> None:
        """Remove all mappings for a conversation."""
        self._conversations.pop(conversation_id, None)
        self._persist()

    def _get_or_create_conversation(self, conversation_id: str) -> ConversationVault:
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = ConversationVault(conversation_id)
        return self._conversations[conversation_id]

    def _save_to_disk(self) -> None:
        if self._persistence_path is None:
            return
        data = {
            conv_id: conv.to_dict()
            for conv_id, conv in self._conversations.items()
        }
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._persistence_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def _save_to_sqlite(self) -> None:
        if self._sqlite_path is None:
            return
        import sqlite3
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._sqlite_path))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS entries (
                conversation_id TEXT NOT NULL,
                fake_value TEXT NOT NULL,
                real_value TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                created_at TEXT,
                expires_at TEXT,
                PRIMARY KEY (conversation_id, fake_value)
            )""")
            conn.execute("DELETE FROM entries")
            for conv_id, conv in self._conversations.items():
                for entry in conv.to_dict()["entries"].values():
                    conn.execute(
                        "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?)",
                        (conv_id, entry["fake_value"], entry["real_value"],
                         entry["entity_type"], entry["created_at"], entry["expires_at"]),
                    )
            conn.commit()
        finally:
            conn.close()

    def _load_from_sqlite(self) -> None:
        if self._sqlite_path is None or not self._sqlite_path.exists():
            return
        import sqlite3
        try:
            conn = sqlite3.connect(str(self._sqlite_path))
            try:
                rows = conn.execute(
                    "SELECT conversation_id, fake_value, real_value, entity_type, created_at, expires_at FROM entries"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            return  # table doesn't exist yet
        for conv_id, fake_value, real_value, entity_type, created_at, expires_at in rows:
            conv = self._get_or_create_conversation(conv_id)
            entry = VaultEntry(
                fake_value=fake_value,
                real_value=real_value,
                entity_type=entity_type,
                created_at=datetime.fromisoformat(created_at) if created_at else datetime.utcnow(),
                expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
            )
            if not entry.is_expired():
                conv.entries[fake_value] = entry
                conv._reverse[real_value] = fake_value

    def _load_from_disk(self) -> None:
        if self._persistence_path is None or not self._persistence_path.exists():
            return
        with open(self._persistence_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for conv_id, conv_data in data.items():
            conv = ConversationVault(conv_id)
            for fake_value, entry_data in conv_data.get("entries", {}).items():
                created = datetime.fromisoformat(entry_data["created_at"])
                expires = datetime.fromisoformat(entry_data["expires_at"]) if entry_data.get("expires_at") else None
                entry = VaultEntry(
                    fake_value=entry_data["fake_value"],
                    real_value=entry_data["real_value"],
                    entity_type=entry_data["entity_type"],
                    created_at=created,
                    expires_at=expires,
                )
                if not entry.is_expired():
                    conv.entries[fake_value] = entry
                    conv._reverse[entry.real_value] = fake_value
            self._conversations[conv_id] = conv


# Global singleton instance
_vault_instance: Optional[VaultStore] = None


def get_vault() -> VaultStore:
    """Get the global vault instance."""
    global _vault_instance
    if _vault_instance is None:
        import os
        default_sqlite = str(Path.home() / ".pii_gateway_vault.db")
        _vault_instance = VaultStore(sqlite_path=os.getenv("VAULT_DB_PATH", default_sqlite))
    return _vault_instance
