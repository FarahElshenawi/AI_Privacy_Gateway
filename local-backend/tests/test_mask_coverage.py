"""/mask honesty and responsiveness: coverage fields, strict mode, size cap, non-blocking."""
import logging
import threading
import time

from fastapi.testclient import TestClient

import app.api.mask as mask_api
from app.main import app
from app.pipeline import engine
from app.security import origin_check
from app.security.auth import get_install_token
from dlp_core import DetectionPipeline, DetectorSpec, UnavailableDetector
from dlp_core.tier1 import Tier1Engine
from dlp_core.masker import RequestBlockedError

origin_check.ALLOWED_HOSTS = origin_check.ALLOWED_HOSTS | {"testclient"}
client = TestClient(app, base_url="http://127.0.0.1:8765")
AUTH = {"Authorization": f"Bearer {get_install_token()}"}


def degraded_pipeline():
    return DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True),
                              DetectorSpec(UnavailableDetector("tier2", ["PERSON", "ORGANIZATION"]))])


def test_coverage_is_reported_and_non_strict_still_sends(monkeypatch):
    p = degraded_pipeline()
    monkeypatch.setattr(engine, "detect", lambda t: p.run(t))
    r = client.post("/api/mask", json={"text": "mail a@b.com", "strict": False}, headers=AUTH).json()
    assert r["degraded"] and not r["coverage_complete"] and r["safe_to_send"] is True
    assert r["uncovered_labels"] == ["ORGANIZATION", "PERSON"] and r["degraded_reasons"] == ["tier2:DetectorUnavailable"]


def test_strict_mode_makes_incomplete_coverage_unsafe(monkeypatch):
    p = degraded_pipeline()
    monkeypatch.setattr(engine, "detect", lambda t: p.run(t))
    r = client.post("/api/mask", json={"text": "mail a@b.com", "strict": True}, headers=AUTH).json()
    assert r["safe_to_send"] is False and r["strict"] is True and "a@b.com" not in r["masked_text"]
    monkeypatch.setattr(engine, "STRICT_DEFAULT", True)                      # env default applies when the request is silent
    assert client.post("/api/mask", json={"text": "hi"}, headers=AUTH).json()["strict"] is True


def test_complete_coverage_is_safe_even_in_strict_mode(monkeypatch):
    p = DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True)])
    monkeypatch.setattr(engine, "detect", lambda t: p.run(t))
    r = client.post("/api/mask", json={"text": "card 4242 4242 4242 4242", "strict": True}, headers=AUTH).json()
    assert r["coverage_complete"] and r["safe_to_send"] and r["leaks"] == [] and r["uncovered_labels"] == []


def test_blocked_detection_returns_503(monkeypatch):
    class Boom:
        name, labels = "t1", frozenset({"EMAIL"})
        def scan(self, t): raise RuntimeError
    p = DetectionPipeline([DetectorSpec(Boom(), critical=True)])
    monkeypatch.setattr(engine, "detect", lambda t: p.run(t))
    assert client.post("/api/mask", json={"text": "x"}, headers=AUTH).status_code == 503


def test_oversized_text_is_rejected_with_413(monkeypatch):
    monkeypatch.setattr(engine, "MAX_TEXT_CHARS", 50)
    assert client.post("/api/mask", json={"text": "x" * 51}, headers=AUTH).status_code == 413
    assert client.post("/api/detect", json={"text": "x" * 51}, headers=AUTH).status_code == 413


def test_slow_detection_does_not_freeze_other_requests(monkeypatch):
    def slow(t):
        time.sleep(1.0)
        return DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True)]).run(t)
    monkeypatch.setattr(engine, "detect", slow)
    done = {}

    def call():
        done["mask"] = client.post("/api/mask", json={"text": "hi"}, headers=AUTH).status_code
    th = threading.Thread(target=call)
    th.start()
    time.sleep(0.2)
    t0 = time.perf_counter()
    assert client.get("/health").status_code == 200
    assert time.perf_counter() - t0 < 0.5          # would be ~0.8s if /mask blocked the event loop
    th.join()
    assert done["mask"] == 200


def test_health_reports_tier_status_and_token_needs_loopback():
    h = client.get("/health").json()
    assert set(h["tiers"]) == {"tier1", "tier2"} and h["tiers"]["tier1"] == "ready"
    assert client.get("/token").status_code == 200
    origin_check.ALLOWED_HOSTS.discard("testclient")
    try:
        assert client.get("/token").status_code == 403
    finally:
        origin_check.ALLOWED_HOSTS.add("testclient")


def test_block_action_returns_422_with_policy_reason(monkeypatch):
    """When the OffsetMasker raises RequestBlockedError, /mask must return 422
    (not 500) with a JSON detail carrying entity_type and rule — never the value."""
    def boom(*args, **kwargs):
        raise RequestBlockedError("INTERNAL_PROJECT_NAME", "policy:DENY_TERM")
    monkeypatch.setattr(engine._masker, "mask", boom)
    res = client.post("/api/mask", json={"text": "x"}, headers=AUTH)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["reason"] == "blocked_by_policy"
    assert detail["entity_type"] == "INTERNAL_PROJECT_NAME"
    assert detail["rule"] == "policy:DENY_TERM"
    # No trace of the original text in the response body
    assert "x" not in res.text or res.text.count("x") <= 2  # only JSON structural chars


# --- Conversation ID validation (mask + demask) ----------------------------

def test_mask_rejects_invalid_conversation_id():
    """conversation_id is used as a vault key — must reject unsafe characters."""
    bad_ids = ["", "   ", "has space", "has/slash", "has'quote", "x" * 65, "has\nnewline"]
    for cid in bad_ids:
        res = client.post("/api/mask", json={"text": "hi", "conversation_id": cid}, headers=AUTH)
        assert res.status_code == 422, f"should reject {cid!r}, got {res.status_code}"


def test_demask_rejects_invalid_conversation_id():
    bad_ids = ["", "has space", "has/slash", "x" * 65]
    for cid in bad_ids:
        res = client.post("/api/demask", json={"text": "hi", "conversation_id": cid}, headers=AUTH)
        assert res.status_code == 422, f"should reject {cid!r}, got {res.status_code}"


# --- Demask audit logging ---------------------------------------------------

def test_demask_emits_audit_record_with_no_text(caplog):
    """Every /demask call must emit a demask_event audit record with metadata only.
    The record must NEVER contain the input text, the restored text, or any substring."""
    text = "the user typed sensitive stuff here"
    with caplog.at_level(logging.INFO, logger="privacy_gateway.audit"):
        res = client.post("/api/demask",
                          json={"text": text, "conversation_id": "audit_test"},
                          headers=AUTH)
    assert res.status_code == 200
    log = "\n".join(r.getMessage() for r in caplog.records if "demask_event" in r.getMessage())
    assert log, "a demask_event audit record must be emitted"
    assert "event_type=demask" in log
    assert "conversation_id=audit_test" in log
    assert f"text_length={len(text)}" in log
    # The text itself must NEVER appear in the audit log
    assert text not in log
    for i in range(len(text) - 5):
        assert text[i:i + 6] not in log


def test_demask_audit_records_replacements_count(caplog):
    """When demask restores real values, the audit must record the count."""
    email = "auditcount@example.com"
    mask_res = client.post("/api/mask",
                           json={"text": f"contact {email}", "conversation_id": "cnt_test"},
                           headers=AUTH)
    assert mask_res.status_code == 200
    masked = mask_res.json()["masked_text"]
    assert email not in masked

    with caplog.at_level(logging.INFO, logger="privacy_gateway.audit"):
        demask_res = client.post("/api/demask",
                                 json={"text": masked, "conversation_id": "cnt_test"},
                                 headers=AUTH)
    assert demask_res.status_code == 200
    assert demask_res.json()["replacements_made"] >= 1
    log = "\n".join(r.getMessage() for r in caplog.records if "demask_event" in r.getMessage())
    assert "replacements_made=" in log
    # The real email must never appear in the audit log
    assert email not in log


# --- Demask rate limiting ---------------------------------------------------

def test_demask_rate_limit_returns_429(monkeypatch):
    """After _DEMASK_MAX_CALLS in the window, further calls return 429."""
    import app.api.demask as demask_api
    monkeypatch.setattr(demask_api, "_DEMASK_MAX_CALLS", 3)
    monkeypatch.setattr(demask_api, "_DEMASK_WINDOW_S", 60)
    # Clear any prior state
    demask_api._DEMASK_CALLS.clear()
    for i in range(3):
        r = client.post("/api/demask", json={"text": "x", "conversation_id": "rl_test"}, headers=AUTH)
        assert r.status_code == 200, f"call {i} should succeed"
    # 4th call should be rate-limited
    r = client.post("/api/demask", json={"text": "x", "conversation_id": "rl_test"}, headers=AUTH)
    assert r.status_code == 429
    assert "rate limit" in r.json()["detail"].lower()


def test_demask_rate_limit_is_per_conversation(monkeypatch):
    """Rate limit on conversation A does not block conversation B."""
    import app.api.demask as demask_api
    monkeypatch.setattr(demask_api, "_DEMASK_MAX_CALLS", 2)
    monkeypatch.setattr(demask_api, "_DEMASK_WINDOW_S", 60)
    demask_api._DEMASK_CALLS.clear()
    # Exhaust conversation A
    for _ in range(2):
        client.post("/api/demask", json={"text": "x", "conversation_id": "conv_a"}, headers=AUTH)
    # Conversation B should still work
    r = client.post("/api/demask", json={"text": "x", "conversation_id": "conv_b"}, headers=AUTH)
    assert r.status_code == 200
    # And conversation A is still blocked
    r = client.post("/api/demask", json={"text": "x", "conversation_id": "conv_a"}, headers=AUTH)
    assert r.status_code == 429
