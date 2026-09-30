"""Vault package."""
from app.vault.store import VaultStore, get_vault
from app.vault.models import VaultEntry, ConversationVault
from app.vault.collision import CollisionError, generate_unique_fake

__all__ = [
    "VaultStore",
    "get_vault",
    "VaultEntry",
    "ConversationVault",
    "CollisionError",
    "generate_unique_fake",
]
