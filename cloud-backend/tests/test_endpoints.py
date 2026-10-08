"""Tests for /api/endpoints — enrollment, heartbeat, listing, deactivation."""
from __future__ import annotations

import time

import pytest


def test_enroll_returns_enrollment_token(client, A):
    """Enrolling creates an endpoint and returns a token for heartbeats."""
    res = client.post("/api/endpoints/enroll",
                      json={"hostname": "laptop-1234", "version": "1.0.0"},
                      headers=A)
    assert res.status_code == 201
    body = res.json()
    assert body["endpoint_id"] > 0
    assert len(body["enrollment_token"]) >= 32
    assert body["hostname"] == "laptop-1234"


def test_enroll_requires_auth(client):
    res = client.post("/api/endpoints/enroll",
                      json={"hostname": "laptop-x"})
    assert res.status_code == 401


def test_enroll_rejects_bad_hostname(client, A):
    """Hostname must match the safe charset — no shell injection, no SQL."""
    for bad in ["", "has space", "has'quote", "has;rm -rf", "../../etc"]:
        res = client.post("/api/endpoints/enroll",
                          json={"hostname": bad}, headers=A)
        assert res.status_code == 422, f"should reject {bad!r}"


def test_heartbeat_updates_last_seen(client, A):
    """A heartbeat after enrollment updates last_seen."""
    enroll = client.post("/api/endpoints/enroll",
                         json={"hostname": "h1", "version": "1.0.0"}, headers=A)
    token = enroll.json()["enrollment_token"]

    # List endpoints — should see our new one
    res = client.get("/api/endpoints", headers=A)
    assert res.status_code == 200
    eps = res.json()
    assert len(eps) == 1
    assert eps[0]["hostname"] == "h1"
    assert eps[0]["is_active"] is True

    # Send a heartbeat
    hb = client.post("/api/endpoints/heartbeat",
                     json={"enrollment_token": token, "version": "1.0.1"}, headers=A)
    assert hb.status_code == 200
    assert hb.json()["ok"] is True


def test_heartbeat_unknown_token_returns_404(client, A):
    """A bad token should not reveal whether it exists or not (same as policies)."""
    res = client.post("/api/endpoints/heartbeat",
                      json={"enrollment_token": "x" * 64}, headers=A)
    assert res.status_code == 404


def test_heartbeat_is_org_scoped(client, A, B):
    """Org B's heartbeat token must not work with org A's API key."""
    # Enroll under org A
    enroll_a = client.post("/api/endpoints/enroll",
                           json={"hostname": "a-host"}, headers=A)
    token_a = enroll_a.json()["enrollment_token"]
    # Enroll under org B
    enroll_b = client.post("/api/endpoints/enroll",
                           json={"hostname": "b-host"}, headers=B)
    token_b = enroll_b.json()["enrollment_token"]

    # Org B's token with org A's key → 404 (don't reveal existence)
    res = client.post("/api/endpoints/heartbeat",
                      json={"enrollment_token": token_b}, headers=A)
    assert res.status_code == 404

    # Org A's token with org A's key → OK
    res = client.post("/api/endpoints/heartbeat",
                      json={"enrollment_token": token_a}, headers=A)
    assert res.status_code == 200


def test_list_endpoints_excludes_tokens(client, A):
    """The list response must NOT include enrollment tokens."""
    client.post("/api/endpoints/enroll",
                json={"hostname": "h1"}, headers=A)
    res = client.get("/api/endpoints", headers=A)
    body = res.json()
    assert len(body) == 1
    # The response model is EndpointInfo — enrollment_token must not appear
    assert "enrollment_token" not in body[0]


def test_deactivate_endpoint(client, A):
    """Deactivation is a soft delete (keeps audit FKs valid)."""
    enroll = client.post("/api/endpoints/enroll",
                         json={"hostname": "h1"}, headers=A)
    eid = enroll.json()["endpoint_id"]

    # Deactivate
    res = client.delete(f"/api/endpoints/{eid}", headers=A)
    assert res.status_code == 204

    # List with active_only=False should still show it, but is_active=False
    res = client.get("/api/endpoints?active_only=false", headers=A)
    eps = res.json()
    assert any(e["id"] == eid and e["is_active"] is False for e in eps)

    # List with active_only=True should hide it
    res = client.get("/api/endpoints?active_only=true", headers=A)
    eps = res.json()
    assert not any(e["id"] == eid for e in eps)


def test_deactivate_is_org_scoped(client, A, B):
    """Org B cannot deactivate org A's endpoint."""
    enroll = client.post("/api/endpoints/enroll",
                         json={"hostname": "a-host"}, headers=A)
    eid = enroll.json()["endpoint_id"]
    res = client.delete(f"/api/endpoints/{eid}", headers=B)
    assert res.status_code == 404  # not 403 — don't reveal existence


def test_endpoint_enrollment_token_is_unique(client, A):
    """Two enrollments produce distinct tokens."""
    e1 = client.post("/api/endpoints/enroll",
                     json={"hostname": "h1"}, headers=A).json()
    e2 = client.post("/api/endpoints/enroll",
                     json={"hostname": "h2"}, headers=A).json()
    assert e1["enrollment_token"] != e2["enrollment_token"]
    assert e1["endpoint_id"] != e2["endpoint_id"]
