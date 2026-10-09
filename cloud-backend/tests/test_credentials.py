"""The credential split: what each secret can and cannot do."""
from __future__ import annotations

import pytest


def test_device_token_can_write_audit_and_read_policies_only(client, A, device):
    hdr, eid = device
    # allowed
    r = client.post("/api/audit", headers=hdr, json={"event_type": "mask", "entity_types": {"EMAIL": 1}, "entity_count": 1})
    assert r.status_code == 201
    exp = client.get("/api/policies/export", headers=hdr)
    assert exp.status_code == 200 and "EMAIL" in exp.json()
    # forbidden: everything else
    for method, path, body in [
        ("GET", "/api/audit", None), ("GET", "/api/audit/stats", None), ("DELETE", "/api/audit/1", None),
        ("GET", "/api/policies", None), ("GET", "/api/policies/EMAIL", None),
        ("POST", "/api/policies", {"entity_type": "X", "action": "redact"}),
        ("PUT", "/api/policies/1", {"action": "redact"}), ("DELETE", "/api/policies/1", None),
        ("GET", "/api/endpoints", None), ("DELETE", f"/api/endpoints/{eid}", None),
        ("GET", "/api/admin/log", None), ("POST", "/api/admin/rotate-enroll-key", None),
    ]:
        assert client.request(method, path, json=body, headers=hdr).status_code == 401, (method, path)


def test_enrollment_key_can_only_enroll(client, E):
    for method, path, body in [
        ("GET", "/api/policies/export", None), ("GET", "/api/audit", None), ("POST", "/api/audit", {"event_type": "mask"}),
        ("GET", "/api/endpoints", None), ("POST", "/api/endpoints/heartbeat", {}),
    ]:
        assert client.request(method, path, json=body, headers=E).status_code == 401, (method, path)


def test_admin_key_cannot_be_used_as_the_enrollment_key(client, A):
    assert client.post("/api/endpoints/enroll", json={"hostname": "h"}, headers={"X-Enroll-Key": A["X-API-Key"]}).status_code == 401


def test_a_device_can_only_report_as_itself(client, device, A):
    hdr, eid = device
    ok = client.post("/api/audit", headers=hdr, json={"event_type": "mask", "entity_count": 1})
    assert ok.status_code == 201
    other = client.post("/api/audit", headers=hdr, json={"event_type": "mask", "endpoint_id": eid + 99})
    assert other.status_code == 403
    from app.db import AuditEvent, get_session, init_db
    s = get_session(init_db())
    try:
        assert {e.endpoint_id for e in s.query(AuditEvent).all()} == {eid}
    finally:
        s.close()


def test_device_tokens_do_not_cross_organizations(client, A, B, EB, device):
    hdr, _ = device
    other = client.post("/api/endpoints/enroll", json={"hostname": "b"}, headers=EB).json()
    ev = client.post("/api/audit", headers={"X-Endpoint-Token": other["endpoint_token"]},
                     json={"event_type": "mask", "entity_count": 1}).json()
    assert client.get("/api/audit", headers=A).json() == []                 # org A sees nothing of org B's device
    assert [e["id"] for e in client.get("/api/audit", headers=B).json()] == [ev["id"]]
    assert client.post("/api/audit", headers=hdr, json={"event_type": "mask"}).status_code == 201


def test_rotating_the_enrollment_key_keeps_devices_working(client, A, E, device):
    hdr, _ = device
    new = client.post("/api/admin/rotate-enroll-key", headers=A).json()["enroll_key"]
    assert client.post("/api/endpoints/enroll", json={"hostname": "x"}, headers=E).status_code == 401   # old key dead
    assert client.post("/api/endpoints/enroll", json={"hostname": "x"}, headers={"X-Enroll-Key": new}).status_code == 201
    assert client.post("/api/endpoints/heartbeat", json={}, headers=hdr).status_code == 200            # device unaffected


def test_admin_actions_are_logged_including_audit_deletions(client, A, device):
    hdr, eid = device
    ev = client.post("/api/audit", headers=hdr, json={"event_type": "mask", "entity_count": 1}).json()
    assert client.delete(f"/api/audit/{ev['id']}", headers=A).status_code == 204
    pid = client.get("/api/policies/EMAIL", headers=A).json()["id"]
    client.put(f"/api/policies/{pid}", headers=A, json={"action": "redact"})
    client.delete(f"/api/endpoints/{eid}", headers=A)
    log = client.get("/api/admin/log", headers=A).json()
    actions = [(e["action"], e["target"]) for e in log]
    assert ("audit.delete", f"event:{ev['id']}") in actions
    assert any(a == "policy.update" for a, _ in actions)
    assert ("endpoint.deactivate", f"endpoint:{eid}") in actions


def test_old_database_is_migrated_and_old_plaintext_tokens_are_hashed(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE,
            api_key VARCHAR(64) NOT NULL UNIQUE, is_active BOOLEAN, created_at DATETIME);
        CREATE TABLE endpoints (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, hostname VARCHAR(255),
            enrollment_token VARCHAR(64) UNIQUE, last_seen DATETIME, version VARCHAR(32), is_active BOOLEAN, created_at DATETIME);
        INSERT INTO organizations VALUES (1,'Old Co','%s',1,'2026-01-01');
        INSERT INTO endpoints (id, org_id, hostname, enrollment_token, is_active) VALUES (1,1,'h','oldplaintexttoken',1);
    """ % ("0" * 64))
    con.commit(); con.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.delenv("CLOUD_ENROLL_KEY", raising=False)
    monkeypatch.delenv("CLOUD_ADMIN_API_KEY", raising=False)
    from app.db import Endpoint, Organization, get_session, hash_api_key, init_db, seed_defaults
    eng = init_db()
    seed_defaults(eng)
    s = get_session(eng)
    try:
        ep = s.get(Endpoint, 1)
        assert ep.enrollment_token is None and ep.token_hash == hash_api_key("oldplaintexttoken")
        assert s.query(Organization).first().enroll_key           # an enrollment key was issued on upgrade
    finally:
        s.close()


def test_enroll_and_admin_keys_must_differ(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    monkeypatch.setenv("CLOUD_ADMIN_API_KEY", "k" * 32)
    monkeypatch.setenv("CLOUD_ENROLL_KEY", "k" * 32)
    from app.db import init_db, seed_defaults
    with pytest.raises(RuntimeError):
        seed_defaults(init_db())
