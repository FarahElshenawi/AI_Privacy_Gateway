"""/mask honesty and responsiveness: coverage fields, strict mode, size cap, non-blocking."""
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

origin_check.ALLOWED_HOSTS = origin_check.ALLOWED_HOSTS | {"testclient"}
client = TestClient(app)
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
