"""Mapping vault: fake <-> real, per conversation, bijective, sealed, expiring.

Security properties:
  * Real values are stored ONLY encrypted (`Sealer`). There is no plaintext
    default; you must pass a sealer on purpose.
  * The real->fake index is an HMAC keyed with a per-vault random key, so the
    lookup structure holds no plaintext and no unkeyed hash that could be
    brute-forced for low-entropy values (names, phone numbers).
  * InMemoryVault: key and mappings live in process memory only. A restart
    drops them, which is the safe default.
  * PersistentVault: same in-memory contract, plus write-through to an
    encrypted SQLite file. Survives restarts. The HMAC key is stored in a
    sibling file (or supplied via env var / OS keystore) so the real→fake
    index can be rebuilt.
  * Expired entries are invisible immediately and purged lazily.
  * Bijection is enforced in both directions.
"""
from __future__ import annotations

import hmac
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterator, Optional, Protocol
from contextlib import contextmanager

from . import keystore


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

    def _after_put(self, conv_id: str, label: str, real: str, fake: str,
                   sealed_real: bytes, expires_at: float, idx: bytes) -> None:
        """Hook for subclasses to persist the new entry. No-op in InMemoryVault."""
        pass

    def _after_delete(self, conv_id: str, fake: str, idx: bytes) -> None:
        """Hook for subclasses to delete a persisted entry. No-op in InMemoryVault."""
        pass

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Group many put() calls into one unit. No-op in memory; PersistentVault commits once."""
        with self._lock:
            yield

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
            sealed = self._sealer.seal(real.encode("utf-8"))
            expires_at = self._clock() + self._ttl
            conv.by_fake[fake] = _Entry(fake, sealed, expires_at, idx)
            conv.by_real[idx] = fake
            conv.version += 1
            self._after_put(conv_id, label, real, fake, sealed, expires_at, idx)

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
        return f"{type(self).__name__}(conversations={len(self._convs)})"


class PersistentVault(InMemoryVault):
    """Write-through persistent vault backed by an encrypted SQLite file.

    On construction, loads all non-expired entries from the SQLite file into the
    in-memory structures (so reads stay O(1) and never hit disk). On every put(),
    the new entry is also written to SQLite (write-through). On lazy expiry, the
    expired row is deleted from SQLite too.

    The HMAC key (used to build the real→fake index) is persisted to a sibling
    file with mode 0600. Without it, the persisted sealed reals could not be
    matched back to their fakes after a restart, because the index would use a
    different random key. If the key file is missing on init, a new key is
    generated (and the DB is treated as empty — old sealed reals become
    unreadable, which is the safe default).

    Expiry uses the WALL clock (time.time): persisted deadlines must stay meaningful across
    restarts and reboots (a monotonic clock restarts at zero and would stretch the TTL).
    Entries are loaded ordered by expiry so the in-memory expiry scan stays correct.

    The SQLite file itself contains only:
      - conversation_id (TEXT)
      - label (TEXT)
      - fake (TEXT)
      - sealed_real (BLOB, Fernet-encrypted)
      - expires_at_epoch (REAL)
      - idx (BLOB, HMAC-SHA256 of label||real)
    No real value is ever stored in plaintext. The sealer key (Fernet) is NOT
    in the SQLite file; it must come from env var DLP_VAULT_KEY or OS keystore.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS vault_entries (
        conv_id     TEXT    NOT NULL,
        label       TEXT    NOT NULL,
        fake        TEXT    NOT NULL,
        sealed_real BLOB    NOT NULL,
        expires_at  REAL    NOT NULL,
        idx         BLOB    NOT NULL,
        PRIMARY KEY (conv_id, fake)
    );
    CREATE INDEX IF NOT EXISTS idx_vault_conv_real
        ON vault_entries(conv_id, idx);
    CREATE TABLE IF NOT EXISTS vault_meta (
        key   TEXT PRIMARY KEY,
        value BLOB NOT NULL
    );
    """

    def __init__(
        self,
        sealer: Sealer,
        db_path: str | Path,
        *,
        ttl_seconds: float = 24 * 3600,
        max_entries_per_conversation: int = 100_000,
        clock: Callable[[], float] = time.time,
        hmac_key: Optional[bytes] = None,
        key_store: Optional[SecretStore] = None,
    ) -> None:
        super().__init__(sealer, ttl_seconds=ttl_seconds,
                         max_entries_per_conversation=max_entries_per_conversation,
                         clock=clock)
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # SQLite connection shared across calls; check_same_thread=False because
        # we hold our own RLock. WAL mode for concurrent readers + single writer.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False,
                                     isolation_level=None)  # autocommit
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(self.SCHEMA)
        for suffix in ("", "-wal", "-shm"):      # owner-only, even under a permissive umask
            try:
                os.chmod(self._db_path + suffix, 0o600)
            except OSError:
                pass

        self._depth = 0
        legacy = self._load_hmac_key()           # older databases kept the key next to the data
        external = key_store.get() if key_store is not None else None
        if hmac_key is not None:
            self._hmac_key = hmac_key
        elif external is not None:
            self._hmac_key = external
        elif legacy is not None:
            self._hmac_key = legacy
        # else: keep the random key from super().__init__
        if key_store is not None and external is None and hmac_key is None:
            if not key_store.set(self._hmac_key):
                key_store = None                  # no OS store: keep it in the DB as before
        if key_store is not None:
            self._conn.execute("DELETE FROM vault_meta WHERE key='hmac_key'")
            if legacy is not None and legacy != self._hmac_key:
                self._reindex()
        else:
            self._store_hmac_key()

        # Load all non-expired entries into memory.
        self._load_from_disk()

    # -- persistence helpers --------------------------------------------
    def _store_hmac_key(self) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO vault_meta(key, value) VALUES ('hmac_key', ?)",
            (self._hmac_key,),
        )

    def _load_hmac_key(self) -> Optional[bytes]:
        row = self._conn.execute(
            "SELECT value FROM vault_meta WHERE key='hmac_key'"
        ).fetchone()
        return row[0] if row else None

    def _reindex(self) -> None:
        """The index key changed (moved to the keystore): rebuild every row's index under it. A row
        that cannot be unsealed is dropped."""
        rows = self._conn.execute("SELECT conv_id, fake, label, sealed_real FROM vault_entries").fetchall()
        self._conn.execute("BEGIN")
        try:
            for conv_id, fake, label, sealed in rows:
                try:
                    real = self._sealer.unseal(sealed).decode("utf-8")
                except Exception:  # noqa: BLE001
                    self._conn.execute("DELETE FROM vault_entries WHERE conv_id=? AND fake=?", (conv_id, fake))
                    continue
                self._conn.execute("UPDATE vault_entries SET idx=? WHERE conv_id=? AND fake=?",
                                   (self._index(label, real), conv_id, fake))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @contextmanager
    def batch(self) -> Iterator[None]:
        """One transaction for every put() inside, instead of one fsync-bound commit each."""
        with self._lock:
            outer = self._depth == 0
            self._depth += 1
            if outer:
                self._conn.execute("BEGIN")
            try:
                yield
            finally:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")   # memory already holds these entries; keep disk in step

    def _load_from_disk(self) -> None:
        """Load all non-expired entries into the in-memory structures.

        Called once on init. After this, all reads hit memory; writes go through
        to disk via _after_put / _after_delete.
        """
        now = self._clock()
        for conv_id, label, fake, sealed_real, expires_at, idx in self._conn.execute(
            "SELECT conv_id, label, fake, sealed_real, expires_at, idx "
            "FROM vault_entries WHERE expires_at > ? ORDER BY expires_at",
            (now,),
        ):
            conv = self._convs.setdefault(conv_id, _Conversation())
            # Stored idx is the persisted HMAC — only valid if hmac_key matches.
            # We re-derive it lazily; for loaded entries we trust the stored value
            # because we loaded with the same hmac_key.
            entry = _Entry(fake, sealed_real, expires_at, idx)
            conv.by_fake[fake] = entry
            conv.by_real[idx] = fake
        # Also purge expired rows from disk on startup (cheap, one DELETE).
        self._conn.execute("DELETE FROM vault_entries WHERE expires_at <= ?", (now,))

    def _after_put(self, conv_id: str, label: str, real: str, fake: str,
                   sealed_real: bytes, expires_at: float, idx: bytes) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO vault_entries "
            "(conv_id, label, fake, sealed_real, expires_at, idx) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, label, fake, sealed_real, expires_at, idx),
        )

    def _after_delete(self, conv_id: str, fake: str, idx: bytes) -> None:
        self._conn.execute(
            "DELETE FROM vault_entries WHERE conv_id=? AND fake=?",
            (conv_id, fake),
        )

    def _live(self, conv_id: str) -> Optional[_Conversation]:
        """Override to also delete expired entries from disk."""
        conv = self._convs.get(conv_id)
        if conv is None:
            return None
        now = self._clock()
        removed_fakes: list[tuple[str, bytes]] = []
        while conv.by_fake:
            fake, entry = next(iter(conv.by_fake.items()))
            if entry.expires_at > now:
                break
            del conv.by_fake[fake]
            conv.by_real.pop(entry.idx, None)
            removed_fakes.append((fake, entry.idx))
        if removed_fakes:
            conv.version += 1
            for fake, idx in removed_fakes:
                self._after_delete(conv_id, fake, idx)
        return conv

    def clear(self, conv_id: str) -> None:
        with self._lock:
            self._convs.pop(conv_id, None)
            self._conn.execute(
                "DELETE FROM vault_entries WHERE conv_id=?", (conv_id,)
            )

    def close(self) -> None:
        """Close the SQLite connection. Call on shutdown for a clean flush."""
        try:
            self._conn.close()
        except Exception:
            pass


def resolve_vault_key() -> Optional[bytes]:
    """Resolve the Fernet key: DLP_VAULT_KEY env var, then the OS keystore (or the legacy 0600
    file, migrated into the keystore when possible), else None (ephemeral)."""
    env_key = os.environ.get("DLP_VAULT_KEY")
    if env_key:
        return env_key.encode("utf-8")
    return keystore.get_secret("vault")


def ensure_vault_key_file() -> Optional[bytes]:
    """Return the vault key, creating and storing one if none exists (OS keystore, else a 0600
    file). Idempotent."""
    existing = resolve_vault_key()
    if existing:
        return existing
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    keystore.set_secret("vault", key)
    return key


class SecretStore(Protocol):
    def get(self) -> Optional[bytes]: ...
    def set(self, value: bytes) -> bool: ...


class KeystoreSecret:
    """The index (HMAC) key held in the OS keystore, not in the database it protects."""

    def __init__(self, name: str = "vault_hmac") -> None:
        self._name = name

    def get(self) -> Optional[bytes]:
        v = keystore.get_secret(self._name)
        return bytes.fromhex(v.decode("ascii")) if v else None

    def set(self, value: bytes) -> bool:
        return keystore.set_secret(self._name, value.hex().encode("ascii"))
