"""Tests for app.cloud_sync — the local↔cloud bridge.

These tests focus on the contract:
  - cloud_sync.start() is a no-op when CLOUD_URL or CLOUD_ENROLL_KEY is unset
  - the CloudAuditHandler parses routing_decision and demask_event records
  - the audit buffer drains correctly and drops oldest when full
  - _pull_and_apply_policies updates DEFAULT_ACTIONS in place
  - status() reports state without leaking secrets
"""
from __future__ import annotations

import logging
import os
from unittest.mock import patch, MagicMock

import pytest

from app import cloud_sync
from app.cloud_sync import (
    _CloudAuditBuffer,
    _audit_buffer, _pull_and_apply_policies, status,
)
from dlp_core.policy import Action, DEFAULT_ACTIONS


# --- No-op when disabled ----------------------------------------------------

def test_start_is_noop_without_cloud_url(monkeypatch):
    """If CLOUD_URL is unset, start() must not spawn threads."""
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.delenv("CLOUD_ENROLL_KEY", raising=False)
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "")
    cloud_sync.start()
    # No threads should have been started
    assert len(cloud_sync._threads) == 0


def test_start_is_noop_when_sync_disabled(monkeypatch):
    """If CLOUD_SYNC_ENABLED=false, start() must not spawn threads even with URL+key."""
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "http://fake")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "fake-key")
    monkeypatch.setattr(cloud_sync, "CLOUD_SYNC_ENABLED", False)
    cloud_sync.start()
    assert len(cloud_sync._threads) == 0


# --- record_event: structured, metadata-only ----------------------------------

def test_record_event_is_structured_and_metadata_only():
    cloud_sync._audit_buffer.drain()
    cloud_sync.record_event("mask", entity_types={"person": 2, "EMAIL": 1}, latency_ms=12.7, conversation_id="conv123")
    ev = cloud_sync._audit_buffer.drain()[0]
    assert ev == {"event_type": "mask", "entity_types": {"PERSON": 2, "EMAIL": 1}, "entity_count": 3,
                  "conversation_id": "conv123", "latency_ms": 12}


def test_record_event_sanitises_and_never_raises():
    cloud_sync._audit_buffer.drain()
    cloud_sync.record_event("bogus")                                       # unknown type: ignored
    cloud_sync.record_event("mask", entity_types={"bad label!": 1}, conversation_id="has space")
    cloud_sync.record_event("mask", entity_types={"X": "not-a-number"})    # junk: swallowed
    ev = cloud_sync._audit_buffer.drain()
    assert len(ev) == 1 and ev[0]["entity_types"] == {"UNKNOWN": 1} and ev[0]["conversation_id"] is None


def test_aggregate_sums_counts_averages_latency_and_keeps_every_fail_closed():
    evs = [{"event_type": "mask", "entity_types": {"EMAIL": 1}, "entity_count": 1, "conversation_id": "c", "latency_ms": 10},
           {"event_type": "mask", "entity_types": {"EMAIL": 2}, "entity_count": 2, "conversation_id": "c", "latency_ms": 30},
           {"event_type": "fail_closed", "entity_types": {"POLICY_BLOCK": 1}, "entity_count": 1, "conversation_id": "c"},
           {"event_type": "fail_closed", "entity_types": {"POLICY_BLOCK": 1}, "entity_count": 1, "conversation_id": "c"}]
    out = cloud_sync._aggregate(evs)
    mask = next(e for e in out if e["event_type"] == "mask")
    assert mask["entity_count"] == 3 and mask["entity_types"] == {"EMAIL": 3} and mask["latency_ms"] == 20
    assert sum(e["event_type"] == "fail_closed" for e in out) == 2          # the dashboard counts rows


# --- Buffer behavior --------------------------------------------------------

def test_buffer_drops_oldest_when_full():
    buf = _CloudAuditBuffer(max_size=3)
    for i in range(5):
        buf.append({"i": i})
    items = buf.drain()
    # Only the last 3 should have survived
    assert len(items) == 3
    assert [item["i"] for item in items] == [2, 3, 4]


def test_buffer_drain_returns_and_clears():
    buf = _CloudAuditBuffer(max_size=100)
    buf.append({"a": 1})
    buf.append({"b": 2})
    items = buf.drain()
    assert len(items) == 2
    assert buf.drain() == []


# --- Policy pull ------------------------------------------------------------

@pytest.fixture
def restore_policy():
    """Cloud policy application changes live global state; always put it back."""
    from dlp_core.policy import Policy, reset_policy_to_defaults
    from app.pipeline import engine
    yield
    reset_policy_to_defaults()
    engine._pipeline.set_policy(Policy())


def _mask(text):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.security.auth import get_install_token
    c = TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5555))
    r = c.post("/api/mask", json={"text": text, "conversation_id": "pol"},
               headers={"Authorization": f"Bearer {get_install_token()}"})
    assert r.status_code == 200, r.text
    return r.json()["masked_text"]


def test_cloud_policy_really_changes_masking(monkeypatch, restore_policy):
    """The pulled policy must change what the live pipeline does (not just a dict)."""
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "https://fake")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "fake-key")
    monkeypatch.setattr(cloud_sync, "_endpoint_token", "T" * 43)
    monkeypatch.setattr(cloud_sync, "_token_loaded", True)
    text = "mail bob@example.com"
    assert "bob@example.com" not in _mask(text)
    monkeypatch.setattr(cloud_sync, "_get_json", lambda url, hdr, timeout=5: {"EMAIL": "keep"})
    _pull_and_apply_policies()
    assert "bob@example.com" in _mask(text)           # admin chose KEEP for EMAIL
    monkeypatch.setattr(cloud_sync, "_get_json", lambda url, hdr, timeout=5: {})
    _pull_and_apply_policies()
    assert "bob@example.com" not in _mask(text)       # removed in the cloud -> back to default


def test_cloud_cannot_downgrade_critical_secrets(monkeypatch, restore_policy):
    applied, rejected = cloud_sync.apply_cloud_policies(
        {"CREDIT_CARD": "keep", "API_KEY": "keep", "EMAIL": "redact", "PERSON": "bogus"})
    assert "CREDIT_CARD" in rejected and "API_KEY" in rejected and "PERSON" in rejected
    assert applied == 1
    assert "4242424242424242" not in _mask("card 4242424242424242")


def test_apply_ignores_unknown_actions_and_keeps_defaults(restore_policy):
    applied, rejected = cloud_sync.apply_cloud_policies({"EMAIL": "nonsense"})
    assert applied == 0 and rejected == ["EMAIL"]
    assert "bob@example.com" not in _mask("mail bob@example.com")


def test_plain_http_cloud_url_is_refused():
    assert cloud_sync._url_is_secure("https://cloud.example.com")
    assert cloud_sync._url_is_secure("http://localhost:8000")
    assert not cloud_sync._url_is_secure("http://cloud.example.com")


def test_failed_flush_keeps_events_for_retry(monkeypatch):
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "https://fake")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "k")
    monkeypatch.setattr(cloud_sync, "_endpoint_token", "T" * 43)
    monkeypatch.setattr(cloud_sync, "_token_loaded", True)
    cloud_sync._audit_buffer.drain()
    for _ in range(3):
        cloud_sync._audit_buffer.append({"event_type": "mask", "entity_types": {"EMAIL": 1},
                                         "entity_count": 1, "conversation_id": "c1"})
    monkeypatch.setattr(cloud_sync, "_post_json", lambda *a, **k: None)   # cloud down
    cloud_sync._flush_audit()
    assert len(cloud_sync._audit_buffer) >= 1
    sent = []
    monkeypatch.setattr(cloud_sync, "_post_json", lambda url, hdr, body, timeout=5: sent.append(body) or {})
    cloud_sync._flush_audit()
    assert len(cloud_sync._audit_buffer) == 0
    assert sum(e["entity_count"] for e in sent) == 3                      # nothing lost, aggregated


def test_bad_conversation_id_is_dropped_not_sent():
    assert cloud_sync._safe_conversation_id("ok_id-1") == "ok_id-1"
    assert cloud_sync._safe_conversation_id("has space") is None
    assert cloud_sync._safe_conversation_id("x" * 65) is None


def test_pull_and_apply_policies_noop_without_cloud(monkeypatch):
    """Without CLOUD_URL, _pull_and_apply_policies must be a no-op."""
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "")
    original = dict(DEFAULT_ACTIONS)
    _pull_and_apply_policies()
    assert dict(DEFAULT_ACTIONS) == original


# --- status() --------------------------------------------------------------

def test_status_reports_state_without_secrets(monkeypatch):
    """status() must report config state but NOT the enrollment key."""
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "https://doppel-cloud.example.com")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "super-secret-key-do-not-leak")
    monkeypatch.setattr(cloud_sync, "CLOUD_SYNC_ENABLED", True)
    s = status()
    assert s["enabled"] is True
    assert s["cloud_url"] == "https://doppel-cloud.example.com"
    assert "super-secret-key-do-not-leak" not in str(s)
    assert "api_key" not in s


def test_status_disabled_when_no_url(monkeypatch):
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "")
    s = status()
    assert s["enabled"] is False
    assert s["cloud_url"] is None


# --- Credential split: enrollment key -> per-device token ----------------------

@pytest.fixture
def fresh_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_sync, "TOKEN_FILE", str(tmp_path / "ep.json"))
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "https://fake")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "enroll-key")
    monkeypatch.setattr(cloud_sync, "_endpoint_token", None)
    monkeypatch.setattr(cloud_sync, "_endpoint_id", None)
    monkeypatch.setattr(cloud_sync, "_token_loaded", False)
    return tmp_path / "ep.json"


def test_enroll_uses_only_the_enrollment_key_and_stores_the_token_0600(monkeypatch, fresh_identity):
    import json, stat
    calls = []
    def fake_post(url, hdr, body, timeout=5):
        calls.append((url, dict(hdr)))
        return {"endpoint_id": 7, "endpoint_token": "tok-" + "x" * 40}
    monkeypatch.setattr(cloud_sync, "_post_json", fake_post)
    assert cloud_sync._enroll() is True
    assert calls[0][1] == {"X-Enroll-Key": "enroll-key"}
    saved = json.loads(fresh_identity.read_text())
    assert saved["endpoint_token"].startswith("tok-") and saved["cloud_url"] == "https://fake"
    if os.name != "nt":
        assert stat.S_IMODE(fresh_identity.stat().st_mode) == 0o600
    assert "enroll-key" not in fresh_identity.read_text()


def test_device_calls_use_the_endpoint_token_never_the_enrollment_key(monkeypatch, fresh_identity):
    seen = []
    def fake_post(url, hdr, body, timeout=5):
        seen.append((url, dict(hdr)))
        return {"endpoint_id": 1, "endpoint_token": "T" * 43} if url.endswith("/enroll") else {"ok": True}
    monkeypatch.setattr(cloud_sync, "_post_json", fake_post)
    monkeypatch.setattr(cloud_sync, "_get_json", lambda url, hdr, timeout=5: seen.append((url, dict(hdr))) or {})
    cloud_sync._audit_buffer.drain()
    cloud_sync._audit_buffer.append({"event_type": "mask", "entity_types": {"EMAIL": 1},
                                     "entity_count": 1, "conversation_id": None})
    cloud_sync._heartbeat(); cloud_sync._flush_audit(); cloud_sync._pull_and_apply_policies()
    later = [h for u, h in seen if not u.endswith("/enroll")]
    assert len(later) == 3 and all(h == {"X-Endpoint-Token": "T" * 43} for h in later)


def test_saved_token_is_reused_without_re_enrolling(monkeypatch, fresh_identity):
    import json
    fresh_identity.write_text(json.dumps({"cloud_url": "https://fake", "endpoint_id": 3, "endpoint_token": "S" * 43}))
    monkeypatch.setattr(cloud_sync, "_post_json",
                        lambda url, hdr, body, timeout=5: pytest.fail("must not enroll") if url.endswith("/enroll") else {})
    cloud_sync._heartbeat()
    assert cloud_sync._endpoint_id == 3


def test_token_for_another_cloud_is_ignored(monkeypatch, fresh_identity):
    import json
    fresh_identity.write_text(json.dumps({"cloud_url": "https://other", "endpoint_id": 3, "endpoint_token": "S" * 43}))
    cloud_sync._load_saved_token()
    assert cloud_sync._endpoint_token is None


def test_revoked_device_forgets_its_token_and_re_enrolls(monkeypatch, fresh_identity):
    fresh_identity.write_text('{"cloud_url": "https://fake", "endpoint_id": 3, "endpoint_token": "' + "S" * 43 + '"}')
    def revoked(url, hdr, body, timeout=5):
        raise cloud_sync._Unauthorized()
    monkeypatch.setattr(cloud_sync, "_post_json", revoked)
    cloud_sync._heartbeat()
    assert cloud_sync._endpoint_token is None and not fresh_identity.exists()
    monkeypatch.setattr(cloud_sync, "_post_json",
                        lambda url, hdr, body, timeout=5: {"endpoint_id": 4, "endpoint_token": "N" * 43})
    cloud_sync._heartbeat()
    assert cloud_sync._endpoint_token == "N" * 43


def test_audit_is_kept_when_enrollment_fails(monkeypatch, fresh_identity):
    cloud_sync._audit_buffer.drain()
    cloud_sync._audit_buffer.append({"event_type": "mask", "entity_types": {"EMAIL": 1},
                                     "entity_count": 1, "conversation_id": None})
    monkeypatch.setattr(cloud_sync, "_post_json", lambda *a, **k: None)
    cloud_sync._flush_audit()
    assert len(cloud_sync._audit_buffer) == 1
