"""API handlers queue structured, metadata-only audit events (mask / file / demask / fail_closed)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import cloud_sync
from app.main import app
from app.security.auth import get_install_token

client = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5555))
AUTH = {"Authorization": f"Bearer {get_install_token()}"}


@pytest.fixture(autouse=True)
def fresh_buffer():
    cloud_sync._audit_buffer.drain()
    yield
    cloud_sync._audit_buffer.drain()


def _events():
    return cloud_sync._audit_buffer.drain()


def test_mask_emits_a_mask_event_with_counts_and_latency_but_no_text():
    secret = "bob@example.com"
    r = client.post("/api/mask", json={"text": f"mail {secret}", "conversation_id": "ev1"}, headers=AUTH)
    assert r.status_code == 200
    ev = _events()
    assert len(ev) == 1 and ev[0]["event_type"] == "mask" and ev[0]["entity_types"] == {"EMAIL": 1}
    assert ev[0]["entity_count"] == 1 and ev[0]["latency_ms"] >= 0 and ev[0]["conversation_id"] == "ev1"
    assert secret not in str(ev)


def test_policy_block_emits_fail_closed(monkeypatch):
    from dlp_core.policy import Action, Policy, DEFAULT_ACTIONS
    from app.pipeline import engine
    table = dict(DEFAULT_ACTIONS); table["EMAIL"] = Action.BLOCK
    engine._pipeline.set_policy(Policy(table))
    try:
        r = client.post("/api/mask", json={"text": "mail bob@example.com", "conversation_id": "ev2"}, headers=AUTH)
    finally:
        engine._pipeline.set_policy(Policy())
    assert r.status_code == 422
    ev = _events()
    assert [e["event_type"] for e in ev] == ["fail_closed"] and ev[0]["entity_types"] == {"POLICY_BLOCK": 1}
    assert "bob@example.com" not in str(ev)


def test_strict_degraded_response_is_a_fail_closed_event():
    r = client.post("/api/mask", json={"text": "call Alice", "conversation_id": "ev3", "strict": True}, headers=AUTH)
    body = r.json()
    if body["safe_to_send"]:
        pytest.skip("Tier 2 is available here, so coverage is complete")
    ev = _events()
    assert ev[-1]["event_type"] == "fail_closed" and ev[-1]["entity_types"] == {"DEGRADED_STRICT": 1}


def test_demask_and_mapping_emit_demask_events():
    client.post("/api/mask", json={"text": "mail bob@example.com", "conversation_id": "ev4"}, headers=AUTH)
    _events()
    m = client.post("/api/mapping", json={"conversation_id": "ev4"}, headers=AUTH).json()
    ev = _events()
    assert m["entries"] and [e["event_type"] for e in ev] == ["demask"] and ev[0]["entity_count"] == len(m["entries"])
    fake = m["entries"][0]["fake"]
    client.post("/api/demask", json={"text": f"hello {fake}", "conversation_id": "ev4"}, headers=AUTH)
    assert [e["event_type"] for e in _events()] == ["demask"]


def test_process_file_emits_file_event_and_fail_closed_on_rejection():
    ok = client.post("/api/process_file", files={"file": ("a.txt", b"mail bob@example.com")},
                     data={"conversation_id": "ev5"}, headers=AUTH)
    assert ok.status_code == 200
    ev = _events()
    assert ev[0]["event_type"] == "file" and ev[0]["entity_count"] >= 1 and ev[0]["latency_ms"] >= 0
    bad = client.post("/api/process_file", files={"file": ("x.pdf", b"not a pdf")},
                      data={"conversation_id": "ev5"}, headers=AUTH)
    assert bad.status_code in (400, 422)
    ev = _events()
    assert ev[0]["event_type"] == "fail_closed" and list(ev[0]["entity_types"]) == ["UNKNOWN_FILE_TYPE"]
