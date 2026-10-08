"""Tests for local backend policy management endpoints (/api/policies/*)."""
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.security import origin_check
from dlp_core.policy import (
    Action,
    StoragePolicy,
    get_active_routing_table,
    reset_policy_to_defaults,
    route_label,
)

origin_check.ALLOWED_HOSTS = origin_check.ALLOWED_HOSTS | {"testclient"}


@pytest.fixture(autouse=True)
def clean_policies():
    reset_policy_to_defaults()
    yield
    reset_policy_to_defaults()


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
    assert "Security violation" in res.json()["detail"]

    # Attempt to set US_SSN to KEEP should also fail
    res = client.post("/api/policies/apply", json={"US_SSN": "keep"})
    assert res.status_code == 400
    assert "Security violation" in res.json()["detail"]


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
