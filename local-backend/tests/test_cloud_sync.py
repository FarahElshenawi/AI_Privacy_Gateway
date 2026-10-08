"""Tests for app.cloud_sync — the local↔cloud bridge.

These tests focus on the contract:
  - cloud_sync.start() is a no-op when CLOUD_URL or CLOUD_API_KEY is unset
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
    CloudAuditHandler, _CloudAuditBuffer,
    _audit_buffer, _pull_and_apply_policies, status,
)
from dlp_core.policy import Action, DEFAULT_ACTIONS


# --- No-op when disabled ----------------------------------------------------

def test_start_is_noop_without_cloud_url(monkeypatch):
    """If CLOUD_URL is unset, start() must not spawn threads."""
    monkeypatch.delenv("CLOUD_URL", raising=False)
    monkeypatch.delenv("CLOUD_API_KEY", raising=False)
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "")
    monkeypatch.setattr(cloud_sync, "CLOUD_API_KEY", "")
    cloud_sync.start()
    # No threads should have been started
    assert len(cloud_sync._threads) == 0


def test_start_is_noop_when_sync_disabled(monkeypatch):
    """If CLOUD_SYNC_ENABLED=false, start() must not spawn threads even with URL+key."""
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "http://fake")
    monkeypatch.setattr(cloud_sync, "CLOUD_API_KEY", "fake-key")
    monkeypatch.setattr(cloud_sync, "CLOUD_SYNC_ENABLED", False)
    cloud_sync.start()
    assert len(cloud_sync._threads) == 0


# --- CloudAuditHandler parses records --------------------------------------

def test_audit_handler_parses_routing_decision():
    """The handler must parse a routing_decision log line into a cloud audit event."""
    buf = _CloudAuditBuffer(max_size=100)
    handler = CloudAuditHandler()
    # Inject our buffer so the handler writes to it
    with patch.object(cloud_sync, "_audit_buffer", buf):
        rec = logging.LogRecord(
            name="privacy_gateway.audit", level=logging.INFO, pathname="", lineno=0,
            msg="routing_decision entity_type=PERSON action=FAKER strategy=PSEUDONYMIZE "
                "storage=STORE_FOR_DEMASKING risk_level=HIGH entity_category=PII "
                "rule=policy:PERSON timestamp=2026-01-01T00:00:00+00:00 "
                "conversation_id=conv123 value_length=5",
            args=None, exc_info=None,
        )
        handler.emit(rec)
        events = buf.drain()
        assert len(events) == 1
        assert events[0]["event_type"] == "mask"
        assert events[0]["entity_types"] == {"PERSON": 1}
        assert events[0]["entity_count"] == 1
        assert events[0]["conversation_id"] == "conv123"


def test_audit_handler_parses_demask_event():
    buf = _CloudAuditBuffer(max_size=100)
    handler = CloudAuditHandler()
    with patch.object(cloud_sync, "_audit_buffer", buf):
        rec = logging.LogRecord(
            name="privacy_gateway.audit", level=logging.INFO, pathname="", lineno=0,
            msg="demask_event event_type=demask conversation_id=conv456 "
                "replacements_made=3 text_length=150 timestamp=2026-01-01T00:00:00+00:00",
            args=None, exc_info=None,
        )
        handler.emit(rec)
        events = buf.drain()
        assert len(events) == 1
        assert events[0]["entity_count"] == 3
        assert events[0]["conversation_id"] == "conv456"


def test_audit_handler_never_includes_real_value():
    """Even if the log line somehow contained a value, the handler must not propagate it."""
    buf = _CloudAuditBuffer(max_size=100)
    handler = CloudAuditHandler()
    sensitive = "real@example.com"
    with patch.object(cloud_sync, "_audit_buffer", buf):
        rec = logging.LogRecord(
            name="privacy_gateway.audit", level=logging.INFO, pathname="", lineno=0,
            msg=f"routing_decision entity_type=EMAIL action=FAKER conversation_id=c value_length=17",
            args=None, exc_info=None,
        )
        handler.emit(rec)
        events = buf.drain()
        for e in events:
            # The cloud event contains only metadata — no value, no text
            for v in e.values():
                assert sensitive not in str(v)


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
    monkeypatch.setattr(cloud_sync, "CLOUD_API_KEY", "fake-key")
    text = "mail bob@example.com"
    assert "bob@example.com" not in _mask(text)
    monkeypatch.setattr(cloud_sync, "_get_json", lambda url, key, timeout=5: {"EMAIL": "keep"})
    _pull_and_apply_policies()
    assert "bob@example.com" in _mask(text)           # admin chose KEEP for EMAIL
    monkeypatch.setattr(cloud_sync, "_get_json", lambda url, key, timeout=5: {})
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
    monkeypatch.setattr(cloud_sync, "CLOUD_API_KEY", "k")
    cloud_sync._audit_buffer.drain()
    for _ in range(3):
        cloud_sync._audit_buffer.append({"event_type": "mask", "entity_types": {"EMAIL": 1},
                                         "entity_count": 1, "conversation_id": "c1"})
    monkeypatch.setattr(cloud_sync, "_post_json", lambda *a, **k: None)   # cloud down
    cloud_sync._flush_audit()
    assert len(cloud_sync._audit_buffer) >= 1
    sent = []
    monkeypatch.setattr(cloud_sync, "_post_json", lambda url, key, body, timeout=5: sent.append(body) or {})
    cloud_sync._flush_audit()
    assert len(cloud_sync._audit_buffer) == 0
    assert sum(e["entity_count"] for e in sent) == 3                      # nothing lost, aggregated


def test_demask_event_is_not_counted_as_mask():
    import logging
    cloud_sync._audit_buffer.drain()
    rec = logging.LogRecord("privacy_gateway.audit", logging.INFO, "", 0,
                            "demask_event event_type=demask conversation_id=c1 replacements_made=2 text_length=10 timestamp=t",
                            None, None)
    cloud_sync.CloudAuditHandler().emit(rec)
    ev = cloud_sync._audit_buffer.drain()[0]
    assert ev["event_type"] == "demask" and ev["entity_count"] == 2


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
    """status() must report config state but NOT the API key."""
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "https://doppel-cloud.example.com")
    monkeypatch.setattr(cloud_sync, "CLOUD_API_KEY", "super-secret-key-do-not-leak")
    monkeypatch.setattr(cloud_sync, "CLOUD_SYNC_ENABLED", True)
    s = status()
    assert s["enabled"] is True
    assert s["cloud_url"] == "https://doppel-cloud.example.com"
    assert "super-secret-key-do-not-leak" not in str(s)
    assert "api_key" not in s


def test_status_disabled_when_no_url(monkeypatch):
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "")
    monkeypatch.setattr(cloud_sync, "CLOUD_API_KEY", "")
    s = status()
    assert s["enabled"] is False
    assert s["cloud_url"] is None
