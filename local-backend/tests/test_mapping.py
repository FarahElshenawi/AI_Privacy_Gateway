"""/api/mapping: what the extension uses to restore real values in the page."""
import logging

from fastapi.testclient import TestClient

import app.api.mapping as mapping_api
from app.main import app
from app.security import origin_check
from app.security.auth import get_install_token

origin_check.ALLOWED_HOSTS = origin_check.ALLOWED_HOSTS | {"testclient"}
client = TestClient(app, base_url="http://127.0.0.1:8765")
AUTH = {"Authorization": f"Bearer {get_install_token()}"}


def _mask(text, conv):
    r = client.post("/api/mask", json={"text": text, "conversation_id": conv}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["masked_text"]


def test_mapping_returns_fake_to_real_for_that_conversation_only():
    masked = _mask("mail mapping.one@example.com", "map_a")
    fake = masked.replace("mail ", "")
    _mask("mail other.person@example.com", "map_b")
    r = client.post("/api/mapping", json={"conversation_id": "map_a"}, headers=AUTH).json()
    assert r["changed"] is True
    assert {"fake": fake, "real": "mapping.one@example.com"} in r["entries"]
    assert all(e["real"] != "other.person@example.com" for e in r["entries"])


def test_unchanged_version_returns_no_entries():
    _mask("mail mapping.two@example.com", "map_c")
    first = client.post("/api/mapping", json={"conversation_id": "map_c"}, headers=AUTH).json()
    again = client.post("/api/mapping", json={"conversation_id": "map_c", "since_version": first["version"]},
                        headers=AUTH).json()
    assert again["changed"] is False and again["entries"] == []
    _mask("also mapping.three@example.com", "map_c")        # vault changed -> new version
    third = client.post("/api/mapping", json={"conversation_id": "map_c", "since_version": first["version"]},
                        headers=AUTH).json()
    assert third["changed"] is True and len(third["entries"]) >= 2


def test_unknown_conversation_is_empty_not_an_error():
    r = client.post("/api/mapping", json={"conversation_id": "never_seen"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["entries"] == []


def test_requires_token_and_valid_conversation_id():
    assert client.post("/api/mapping", json={"conversation_id": "x"}).status_code == 401
    assert client.post("/api/mapping", json={"conversation_id": "x"},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/mapping", json={"conversation_id": "bad id!"}, headers=AUTH).status_code == 422


def test_delivery_is_audited_without_values(caplog):
    _mask("mail mapping.audit@example.com", "map_d")
    with caplog.at_level(logging.INFO, logger="privacy_gateway.audit"):
        client.post("/api/mapping", json={"conversation_id": "map_d"}, headers=AUTH)
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "demask_event" in log and "map_d" in log
    assert "mapping.audit@example.com" not in log


def test_rate_limited_per_conversation(monkeypatch):
    monkeypatch.setattr(mapping_api, "_MAX_CALLS", 2)
    mapping_api._CALLS.clear()
    for _ in range(2):
        assert client.post("/api/mapping", json={"conversation_id": "map_rl"}, headers=AUTH).status_code == 200
    assert client.post("/api/mapping", json={"conversation_id": "map_rl"}, headers=AUTH).status_code == 429
    assert client.post("/api/mapping", json={"conversation_id": "map_rl2"}, headers=AUTH).status_code == 200
