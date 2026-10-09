"""Tests for /api/endpoints — enrollment (enrollment key), heartbeat (endpoint token), admin listing."""
from __future__ import annotations

from datetime import datetime


def test_enroll_returns_a_per_device_token(client, E):
    res = client.post("/api/endpoints/enroll", json={"hostname": "laptop-1234", "version": "1.0.0"}, headers=E)
    assert res.status_code == 201
    body = res.json()
    assert body["endpoint_id"] > 0 and len(body["endpoint_token"]) >= 32 and body["hostname"] == "laptop-1234"


def test_enroll_requires_the_enrollment_key(client, A):
    assert client.post("/api/endpoints/enroll", json={"hostname": "x"}).status_code == 401
    assert client.post("/api/endpoints/enroll", json={"hostname": "x"},
                       headers={"X-Enroll-Key": "w" * 32}).status_code == 401
    # the ADMIN key is not an enrollment credential (that is the point of the split)
    assert client.post("/api/endpoints/enroll", json={"hostname": "x"}, headers=A).status_code == 401


def test_enroll_rejects_bad_hostname(client, E):
    for bad in ["", "has space", "has'quote", "has;rm -rf", "../../etc"]:
        assert client.post("/api/endpoints/enroll", json={"hostname": bad}, headers=E).status_code == 422, bad


def test_heartbeat_updates_last_seen_and_version(client, A, device):
    hdr, eid = device
    hb = client.post("/api/endpoints/heartbeat", json={"version": "1.0.1"}, headers=hdr)
    assert hb.status_code == 200 and hb.json()["ok"] is True
    ep = next(e for e in client.get("/api/endpoints", headers=A).json() if e["id"] == eid)
    assert ep["version"] == "1.0.1" and ep["last_seen"] is not None


def test_heartbeat_rejects_unknown_token_and_the_wrong_credential_types(client, A, E, device):
    assert client.post("/api/endpoints/heartbeat", json={}, headers={"X-Endpoint-Token": "x" * 43}).status_code == 401
    assert client.post("/api/endpoints/heartbeat", json={}, headers=A).status_code == 401      # admin key is not a device token
    assert client.post("/api/endpoints/heartbeat", json={}, headers=E).status_code == 401      # nor is the enrollment key


def test_tokens_are_stored_hashed_only(client, device):
    from app.db import Endpoint, get_session, init_db
    hdr, eid = device
    s = get_session(init_db())
    try:
        ep = s.get(Endpoint, eid)
        assert ep.enrollment_token is None
        assert ep.token_hash and hdr["X-Endpoint-Token"] not in (ep.token_hash,)
        assert len(ep.token_hash) == 64
    finally:
        s.close()


def test_list_endpoints_is_admin_only_and_excludes_tokens(client, A, device):
    hdr, _ = device
    assert client.get("/api/endpoints", headers=hdr).status_code == 401      # a device cannot list the fleet
    body = client.get("/api/endpoints", headers=A).json()
    assert len(body) == 1 and body[0]["hostname"] == "dev-1"
    assert not any("token" in k for k in body[0])


def test_deactivated_endpoint_token_stops_working(client, A, device):
    hdr, eid = device
    assert client.delete(f"/api/endpoints/{eid}", headers=A).status_code == 204
    assert client.post("/api/endpoints/heartbeat", json={}, headers=hdr).status_code == 401
    assert any(e["id"] == eid and e["is_active"] is False for e in client.get("/api/endpoints", headers=A).json())
    assert not any(e["id"] == eid for e in client.get("/api/endpoints?active_only=true", headers=A).json())


def test_endpoints_are_org_scoped(client, A, B, EB, device):
    _, eid = device
    assert client.delete(f"/api/endpoints/{eid}", headers=B).status_code == 404       # other org's admin
    other = client.post("/api/endpoints/enroll", json={"hostname": "b-host"}, headers=EB).json()
    assert client.get("/api/endpoints", headers=B).json()[0]["id"] == other["endpoint_id"]
    assert all(e["id"] != other["endpoint_id"] for e in client.get("/api/endpoints", headers=A).json())


def test_tokens_are_unique(client, E):
    e1 = client.post("/api/endpoints/enroll", json={"hostname": "h1"}, headers=E).json()
    e2 = client.post("/api/endpoints/enroll", json={"hostname": "h2"}, headers=E).json()
    assert e1["endpoint_token"] != e2["endpoint_token"] and e1["endpoint_id"] != e2["endpoint_id"]
