"""Mapping vault: fake <-> real, per conversation, bijective, sealed, expiring.

Security properties:
  * Real values are stored ONLY encrypted (`Sealer`). There is no plaintext
    default; you must pass a sealer on purpose.
  * The real->fake index is an HMAC keyed with a per-vault random key, so the
    lookup structure holds no plaintext and no unkeyed hash that could be
    brute-forced for low-entropy values (names, phone numbers).
  * Key and mappings live in process memory only. A restart drops them, which
    is the safe default; persistence must be added deliberately.
  * Expired entries are invisible immediately and purged lazily.
  * Bijection is enforced in both directions.
"""
from __future__ import annotations

import hmac
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Callable, Optional, Protocol


class VaultCollisionError(Exception):
    """A put() would break the one-to-one fake <-> real mapping."""


class VaultCapacityError(Exception):
    """A conversation exceeded its entry limit."""


class Sealer(Protocol):
    def seal(self, plaintext: bytes) -> bytes: ...
    def unseal(self, token: bytes) -> bytes: ...


class FernetSealer:
    """AES-128-CBC + HMAC (Fernet). Requires the `cryptography` package.

    Pass `key` (from your OS keystore) to survive restarts, otherwise an
    ephemeral key is generated and mappings die with the process.
    """

    def __init__(self, key: Optional[bytes] = None) -> None:
        from cryptography.fernet import Fernet  # lazy: only needed if used
        self._f = Fernet(key or Fernet.generate_key())

    def seal(self, plaintext: bytes) -> bytes:
        return self._f.encrypt(plaintext)

    def unseal(self, token: bytes) -> bytes:
        return self._f.decrypt(token)


@dataclass(slots=True)
class _Entry:
    fake: str
    sealed_real: bytes
    expires_at: float
    idx: bytes                # HMAC(label, real): key of this entry in by_real


@dataclass(slots=True)
class _Conversation:
    # Insertion-ordered. Every entry gets the same TTL on a monotonic clock, so expiry order ==
    # insertion order and the expired entries are always a prefix (see InMemoryVault._live).
    by_fake: "OrderedDict[str, _Entry]" = field(default_factory=OrderedDict)
    by_real: dict[bytes, str] = field(default_factory=dict)   # HMAC(label, real) -> fake
    version: int = 0


class InMemoryVault:
    def __init__(
        self,
        sealer: Sealer,
        *,
        ttl_seconds: float = 24 * 3600,
        max_entries_per_conversation: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._sealer = sealer
        self._ttl = ttl_seconds
        self._cap = max_entries_per_conversation
        self._clock = clock
        self._hmac_key = os.urandom(32)
        self._convs: dict[str, _Conversation] = {}
        self._lock = threading.RLock()

    # -- internals -------------------------------------------------------
    def _index(self, label: str, real: str) -> bytes:
        return hmac.digest(self._hmac_key, label.encode() + b"\0" + real.encode("utf-8"), sha256)

    def _live(self, conv_id: str) -> Optional[_Conversation]:
        """Return the conversation with expired entries removed (caller holds lock).

        Entries share one TTL and the clock is monotonic, so they expire in insertion order:
        pop expired entries from the front and stop at the first live one. Cost is O(expired),
        not O(entries), so a lookup stays cheap however large a file's mapping gets.
        """
        conv = self._convs.get(conv_id)
        if conv is None:
            return None
        now = self._clock()
        removed = False
        while conv.by_fake:
            fake, entry = next(iter(conv.by_fake.items()))
            if entry.expires_at > now:
                break
            del conv.by_fake[fake]
            conv.by_real.pop(entry.idx, None)
            removed = True
        if removed:
            conv.version += 1
        return conv

    # -- public API ------------------------------------------------------
    def lookup_fake(self, conv_id: str, label: str, real: str) -> Optional[str]:
        with self._lock:
            conv = self._live(conv_id)
            return None if conv is None else conv.by_real.get(self._index(label, real))

    def fake_in_use(self, conv_id: str, fake: str) -> bool:
        with self._lock:
            conv = self._live(conv_id)
            return conv is not None and fake in conv.by_fake

    def put(self, conv_id: str, label: str, real: str, fake: str) -> None:
        if not real or not fake:
            raise ValueError("real and fake must be non-empty")
        idx = self._index(label, real)
        with self._lock:
            conv = self._live(conv_id) or self._convs.setdefault(conv_id, _Conversation())
            existing_fake = conv.by_real.get(idx)
            if existing_fake is not None:
                if existing_fake == fake:
                    return
                raise VaultCollisionError("real value already mapped to a different fake")
            if fake in conv.by_fake:
                raise VaultCollisionError("fake value already maps to a different real")
            if len(conv.by_fake) >= self._cap:
                raise VaultCapacityError("conversation entry limit reached")
            conv.by_fake[fake] = _Entry(
                fake, self._sealer.seal(real.encode("utf-8")), self._clock() + self._ttl, idx)
            conv.by_real[idx] = fake
            conv.version += 1

    def items(self, conv_id: str) -> list[tuple[str, str]]:
        """(fake, real) pairs, unsealed. Handle the result as sensitive."""
        with self._lock:
            conv = self._live(conv_id)
            if conv is None:
                return []
            return [(f, self._sealer.unseal(e.sealed_real).decode("utf-8"))
                    for f, e in conv.by_fake.items()]

    def version(self, conv_id: str) -> int:
        with self._lock:
            conv = self._live(conv_id)
            return -1 if conv is None else conv.version

    def purge_expired(self) -> None:
        with self._lock:
            for cid in list(self._convs):
                conv = self._live(cid)
                if conv is not None and not conv.by_fake:
                    del self._convs[cid]

    def clear(self, conv_id: str) -> None:
        with self._lock:
            self._convs.pop(conv_id, None)

    def __repr__(self) -> str:  # never expose contents
        return f"InMemoryVault(conversations={len(self._convs)})"
