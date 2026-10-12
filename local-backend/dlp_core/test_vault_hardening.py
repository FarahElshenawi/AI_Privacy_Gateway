"""Vault secrets live in the OS keystore, not beside the data; writes are batched."""
from __future__ import annotations

import os
import sqlite3
import sys
import types

import pytest
from cryptography.fernet import Fernet

from dlp_core import FernetSealer, PersistentVault
from dlp_core import keystore
from dlp_core.vault import KeystoreSecret


@pytest.fixture
def fake_keyring(monkeypatch, tmp_path):
    store: dict = {}
    mod = types.ModuleType("keyring")
    backends = types.ModuleType("keyring.backends")
    fail = types.ModuleType("keyring.backends.fail")

    class FailKeyring:  # noqa: D401
        pass
    fail.Keyring = FailKeyring
    mod.get_keyring = lambda: object()
    mod.get_password = lambda svc, name: store.get((svc, name))
    mod.set_password = lambda svc, name, v: store.__setitem__((svc, name), v)
    backends.fail = fail
    mod.backends = backends
    monkeypatch.setitem(sys.modules, "keyring", mod)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail)
    monkeypatch.delenv("DLP_KEYSTORE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(keystore.Path, "home", classmethod(lambda c: tmp_path))
    return store


def _meta(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT value FROM vault_meta WHERE key='hmac_key'").fetchone()
    finally:
        c.close()


def test_hmac_key_goes_to_keystore_not_database(fake_keyring, tmp_path):
    db, key = str(tmp_path / "v.db"), Fernet.generate_key()
    v = PersistentVault(FernetSealer(key), db, key_store=KeystoreSecret())
    v.put("c", "PERSON", "Alice Smith", "Bob Jones")
    v.close()
    assert _meta(db) is None
    assert (keystore.SERVICE, "vault_hmac") in fake_keyring
    v2 = PersistentVault(FernetSealer(key), db, key_store=KeystoreSecret())
    assert v2.lookup_fake("c", "PERSON", "Alice Smith") == "Bob Jones"


def test_legacy_db_key_is_migrated_and_index_rebuilt(fake_keyring, tmp_path):
    db, key = str(tmp_path / "v.db"), Fernet.generate_key()
    old = PersistentVault(FernetSealer(key), db)              # old behaviour: key in the DB
    old.put("c", "PERSON", "Alice Smith", "Bob Jones")
    old.close()
    assert _meta(db) is not None
    keystore.set_secret("vault_hmac", os.urandom(32).hex().encode())   # a different external key
    new = PersistentVault(FernetSealer(key), db, key_store=KeystoreSecret())
    assert _meta(db) is None
    assert new.lookup_fake("c", "PERSON", "Alice Smith") == "Bob Jones"
    new.close()


def test_without_os_keystore_key_stays_in_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DLP_KEYSTORE", "file")
    monkeypatch.setattr(keystore.Path, "home", classmethod(lambda c: tmp_path))
    db, key = str(tmp_path / "v.db"), Fernet.generate_key()
    v = PersistentVault(FernetSealer(key), db, key_store=KeystoreSecret())
    v.put("c", "PERSON", "Alice Smith", "Bob Jones")
    v.close()
    v2 = PersistentVault(FernetSealer(key), db, key_store=KeystoreSecret())
    assert v2.lookup_fake("c", "PERSON", "Alice Smith") == "Bob Jones"


def test_file_key_migrates_into_keystore_and_file_is_removed(fake_keyring, tmp_path):
    f = tmp_path / ".pii_gateway_vault.key"
    k = Fernet.generate_key()
    f.write_bytes(k)
    assert keystore.get_secret("vault") == k
    assert not f.exists()
    assert keystore.get_secret("vault") == k          # now served from the keystore


def test_batch_commits_once_and_nests(tmp_path):
    db = str(tmp_path / "v.db")
    key = Fernet.generate_key()
    v = PersistentVault(FernetSealer(key), db)
    stmts: list[str] = []
    v._conn.set_trace_callback(stmts.append)
    with v.batch():
        with v.batch():
            for i in range(50):
                v.put("c", "PERSON", f"real{i}", f"fake{i}")
    assert sum(s == "COMMIT" for s in stmts) == 1 and sum(s == "BEGIN" for s in stmts) == 1
    v.close()
    v2 = PersistentVault(FernetSealer(key), db)
    assert len(v2.items("c")) == 50
