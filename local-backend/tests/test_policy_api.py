"""Tests for local backend policy management endpoints (/api/policies/*)."""
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.pipeline import engine
from app.security import origin_check
from app.security.auth import get_install_token
from dlp_core.policy import (
    Action,
    StoragePolicy,
    get_active_routing_table,
    reset_policy_to_defaults,
    route_label,
)

origin_check.ALLOWED_HOSTS = origin_check.ALLOWED_HOSTS | {"testclient"}


AUTH = {"Authorization": f"Bearer {get_install_token()}"}
EXT = "chrome-extension://" + origin_check.PINNED_EXTENSION_ID


class _Client(TestClient):
    """TestClient that sends the install token unless a test says otherwise."""
    def request(self, method, url, **kw):
        if kw.pop("anon", False):
            return super().request(method, url, **kw)
        headers = {**AUTH, **(kw.pop("headers", None) or {})}
        return super().request(method, url, headers=headers, **kw)


TestClient_ = TestClient
TestClient = _Client      # every test below gets an authenticated client


@pytest.fixture(autouse=True)
def clean_policies():
    from dlp_core.policy import Policy
    reset_policy_to_defaults()
    engine._pipeline.set_policy(Policy())
    yield
    reset_policy_to_defaults()
    engine._pipeline.set_policy(Policy())


def test_get_active_policies():
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    res = client.get("/api/policies/active")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "policies" in data
    assert data["total"] > 20
    assert "EMAIL" in data["policies"]
    assert data["policies"]["EMAIL"]["action"] == "faker"


def test_apply_policy_override():
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # Before override: ORGANIZATION is faker
    assert route_label("ORGANIZATION").action == Action.FAKER

    # Apply override: ORGANIZATION -> redact
    res = client.post("/api/policies/apply", json={"OVERRIDES": {"ORGANIZATION": "redact"}})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["applied"] == 1

    # After override: ORGANIZATION is now REDACT
    assert route_label("ORGANIZATION").action == Action.REDACT


def test_apply_flat_dict_override():
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # Cloud backend export format is a flat dict: {"EMAIL": "keep", "PHONE_NUMBER": "redact"}
    res = client.post("/api/policies/apply", json={"EMAIL": "keep", "PHONE_NUMBER": "redact"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["applied"] == 2

    assert route_label("EMAIL").action == Action.KEEP
    assert route_label("PHONE_NUMBER").action == Action.REDACT


def test_apply_policy_rejects_immutable_secret_as_keep():
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # Attempt to set API_KEY to KEEP should fail with 400
    res = client.post("/api/policies/apply", json={"API_KEY": "keep"})
    assert res.status_code == 400
    assert "API_KEY" in res.json()["detail"]

    # Attempt to set US_SSN to KEEP should also fail
    res = client.post("/api/policies/apply", json={"US_SSN": "keep"})
    assert res.status_code == 400
    assert "US_SSN" in res.json()["detail"]

    # All-or-nothing: one bad entry means NOTHING is applied
    res = client.post("/api/policies/apply", json={"EMAIL": "redact", "API_KEY": "keep"})
    assert res.status_code == 400
    assert route_label("EMAIL").action == Action.FAKER


def test_reset_policies():
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    # Override EMAIL to REDACT
    client.post("/api/policies/apply", json={"EMAIL": "redact"})
    assert route_label("EMAIL").action == Action.REDACT

    # Reset
    res = client.post("/api/policies/reset")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # Restored to default FAKER
    assert route_label("EMAIL").action == Action.FAKER


# ── these routes change what gets masked: they need the same protection as /api/mask ─────────

def test_policy_routes_require_the_install_token():
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    for method, path in (("get", "/api/policies/active"), ("post", "/api/policies/apply"), ("post", "/api/policies/reset")):
        body = {"json": {"EMAIL": "keep"}} if "apply" in path else {}
        r = c.request(method.upper(), path, anon=True, **body)
        assert r.status_code == 401, path
        r = c.request(method.upper(), path, anon=True, headers={"Authorization": "Bearer wrong"}, **body)
        assert r.status_code == 401, path
    assert route_label("EMAIL").action == Action.FAKER          # untouched


def test_policy_routes_reject_web_pages_and_other_extensions():
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    for origin in ("http://localhost:5173", "http://localhost:3000", "https://chatgpt.com",
                   "chrome-extension://" + "c" * 32):
        r = c.post("/api/policies/apply", json={"EMAIL": "keep"}, headers={"Origin": origin})
        assert r.status_code == 403, origin
    r = c.post("/api/policies/apply", json={"EMAIL": "keep"}, headers={"Origin": EXT})
    assert r.status_code == 200


def test_policy_routes_reject_dns_rebinding_host():
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    r = c.post("/api/policies/apply", json={"EMAIL": "keep"}, headers={"Host": "attacker.example:8765"})
    assert r.status_code == 403


def test_applied_policy_changes_live_masking_not_just_the_table():
    """The API and cloud sync share one code path, so an applied policy really changes /api/mask."""
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    def mask():
        return c.post("/api/mask", json={"text": "mail zed@example.com", "conversation_id": "pol_api"}).json()["masked_text"]
    assert "zed@example.com" not in mask()
    assert c.post("/api/policies/apply", json={"EMAIL": "keep"}).status_code == 200
    assert "zed@example.com" in mask()
    assert c.post("/api/policies/reset").status_code == 200
    assert "zed@example.com" not in mask()


def test_block_action_is_accepted():
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    assert c.post("/api/policies/apply", json={"EMAIL": "block"}).status_code == 200
    r = c.post("/api/mask", json={"text": "mail zed@example.com", "conversation_id": "pol_blk"})
    assert r.status_code == 422
