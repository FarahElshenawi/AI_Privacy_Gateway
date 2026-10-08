"""Tests for PersistentVault — encrypted SQLite write-through + load-on-init.

The in-memory contract is the same as InMemoryVault (covered by test_core.py and
test_routing_storage.py). These tests focus on persistence-specific behavior:
  - mappings survive a restart (re-instantiation with the same db_path)
  - expired entries are purged from disk on init
  - clear() removes rows from SQLite, not just memory
  - bijection is preserved across restarts
  - the HMAC key is persisted in the DB meta table
  - real values are never stored in plaintext (only Fernet-encrypted BLOBs)

Note: for the sealer key to survive restarts, the Fernet key must be stable.
These tests pass a fixed Fernet key so the sealer can decrypt on reopen.
In production, `resolve_vault_key()` / `ensure_vault_key_file()` handle this.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from dlp_core import FernetSealer, PersistentVault
from dlp_core.vault import VaultCollisionError


@pytest.fixture
def tmp_db():
    """Yield a fresh temp DB path; clean up after."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    os.unlink(db_path)  # remove so PersistentVault creates fresh
    yield db_path
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass


@pytest.fixture
def stable_key() -> bytes:
    """A fixed Fernet key so the sealer can decrypt across restarts."""
    return Fernet.generate_key()


def _vault(db_path: str, key: bytes, **kw) -> PersistentVault:
    return PersistentVault(FernetSealer(key), db_path, **kw)


def test_put_then_reopen_restores_mapping(tmp_db, stable_key):
    """A put() followed by re-instantiation must restore the fake↔real mapping."""
    v1 = _vault(tmp_db, stable_key)
    v1.put("conv1", "EMAIL", "real@example.com", "fake@example.org")
    assert v1.lookup_fake("conv1", "EMAIL", "real@example.com") == "fake@example.org"
    v1.close()

    # Re-open with a new vault instance pointing at the same DB.
    v2 = _vault(tmp_db, stable_key)
    assert v2.lookup_fake("conv1", "EMAIL", "real@example.com") == "fake@example.org"
    pairs = v2.items("conv1")
    assert len(pairs) == 1
    assert pairs[0] == ("fake@example.org", "real@example.com")
    v2.close()


def test_multiple_conversations_survive_restart(tmp_db, stable_key):
    v1 = _vault(tmp_db, stable_key)
    v1.put("conv_a", "PERSON", "Alice", "Anna")
    v1.put("conv_a", "PERSON", "Bob", "Ben")
    v1.put("conv_b", "EMAIL", "x@y.com", "a@b.com")
    v1.close()

    v2 = _vault(tmp_db, stable_key)
    assert v2.lookup_fake("conv_a", "PERSON", "Alice") == "Anna"
    assert v2.lookup_fake("conv_a", "PERSON", "Bob") == "Ben"
    assert v2.lookup_fake("conv_b", "EMAIL", "x@y.com") == "a@b.com"
    assert len(v2.items("conv_a")) == 2
    assert len(v2.items("conv_b")) == 1
    v2.close()


def test_real_value_never_stored_in_plaintext(tmp_db, stable_key):
    """The SQLite file must not contain the real value as plaintext."""
    real = "definitely-plaintext-secret@example.com"
    v1 = _vault(tmp_db, stable_key)
    v1.put("conv", "EMAIL", real, "fake@host.org")
    v1.close()

    db_bytes = Path(tmp_db).read_bytes()
    assert real.encode("utf-8") not in db_bytes, \
        "real value must NOT appear in plaintext in the SQLite file"


def test_expired_entries_purged_on_reopen(tmp_db, stable_key):
    """Entries past their TTL must not be loaded on reopen."""
    v1 = _vault(tmp_db, stable_key, ttl_seconds=0.1)  # 100ms TTL
    v1.put("conv", "PERSON", "Alice", "Anna")
    v1.close()

    # Wait past TTL
    time.sleep(0.2)

    v2 = _vault(tmp_db, stable_key, ttl_seconds=0.1)
    # The expired entry should not be visible
    assert v2.lookup_fake("conv", "PERSON", "Alice") is None
    assert v2.items("conv") == []
    v2.close()


def test_clear_removes_from_disk(tmp_db, stable_key):
    v1 = _vault(tmp_db, stable_key)
    v1.put("conv", "PERSON", "Alice", "Anna")
    v1.clear("conv")
    v1.close()

    v2 = _vault(tmp_db, stable_key)
    assert v2.lookup_fake("conv", "PERSON", "Alice") is None
    assert v2.items("conv") == []
    v2.close()


def test_bijection_preserved_across_restart(tmp_db, stable_key):
    v1 = _vault(tmp_db, stable_key)
    v1.put("conv", "PERSON", "Alice", "Anna")
    v1.close()

    v2 = _vault(tmp_db, stable_key)
    # The real must still map to the same fake, and the fake must still be in use.
    assert v2.lookup_fake("conv", "PERSON", "Alice") == "Anna"
    assert v2.fake_in_use("conv", "Anna") is True
    # A put of a different real to the same fake must still raise.
    with pytest.raises(VaultCollisionError):
        v2.put("conv", "PERSON", "Bob", "Anna")
    # A put of the same real to a different fake must still raise.
    with pytest.raises(VaultCollisionError):
        v2.put("conv", "PERSON", "Alice", "Annie")
    v2.close()


def test_hmac_key_persisted_in_meta(tmp_db, stable_key):
    """The HMAC key must be stored in vault_meta so the index can be rebuilt."""
    v1 = _vault(tmp_db, stable_key)
    v1.put("conv", "PERSON", "Alice", "Anna")
    original_hmac = v1._hmac_key
    v1.close()

    v2 = _vault(tmp_db, stable_key)
    assert v2._hmac_key == original_hmac, "HMAC key must persist across restarts"
    v2.close()


def test_demask_works_across_restart(tmp_db, stable_key):
    """End-to-end: mask with v1, close, demask with v2 — real value restored."""
    from dlp_core import Demasker
    v1 = _vault(tmp_db, stable_key)
    v1.put("conv1", "EMAIL", "real@x.com", "fake@y.com")
    v1.close()

    v2 = _vault(tmp_db, stable_key)
    demasker2 = Demasker(v2)
    text = "contact me at fake@y.com please"
    restored, count = demasker2.restore(text, "conv1")
    assert count == 1
    assert "real@x.com" in restored
    assert "fake@y.com" not in restored
    v2.close()


def test_in_memory_and_persistent_share_interface(tmp_db, stable_key):
    """PersistentVault is a drop-in replacement for InMemoryVault."""
    from dlp_core import InMemoryVault
    real = "Alice"
    fake = "Anna"

    mem = InMemoryVault(FernetSealer(stable_key))
    per = _vault(tmp_db, stable_key)

    mem.put("c", "PERSON", real, fake)
    per.put("c", "PERSON", real, fake)

    assert mem.lookup_fake("c", "PERSON", real) == per.lookup_fake("c", "PERSON", real)
    assert mem.fake_in_use("c", fake) == per.fake_in_use("c", fake)
    assert mem.items("c") == per.items("c")
    assert mem.version("c") == per.version("c")
    per.close()


def test_expiry_survives_restart_on_wall_clock(tmp_path):
    """Persisted deadlines must use wall-clock time (monotonic restarts at 0 after a reboot)."""
    import time
    from dlp_core import FernetSealer, PersistentVault
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    db = tmp_path / "v.db"
    v = PersistentVault(FernetSealer(key), db, ttl_seconds=3600)
    v.put("c", "EMAIL", "a@b.com", "x@y.com")
    row = v._conn.execute("SELECT expires_at FROM vault_entries").fetchone()
    assert abs(row[0] - (time.time() + 3600)) < 5          # epoch seconds, not uptime seconds
    v.close()


def test_loaded_entries_keep_expiry_order_and_db_is_private(tmp_path):
    import os, stat
    from dlp_core import FernetSealer, PersistentVault
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    db = tmp_path / "v.db"
    t = [1000.0]
    v = PersistentVault(FernetSealer(key), db, ttl_seconds=100, clock=lambda: t[0])
    for i in range(3):
        t[0] += 10
        v.put("c", "EMAIL", f"u{i}@b.com", f"f{i}@y.com")
    v.close()
    v2 = PersistentVault(FernetSealer(key), db, ttl_seconds=100, clock=lambda: t[0])
    t[0] = 1000.0 + 10 + 100 + 1        # only the first entry has expired
    assert v2.lookup_fake("c", "EMAIL", "u0@b.com") is None
    assert v2.lookup_fake("c", "EMAIL", "u2@b.com") == "f2@y.com"
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
