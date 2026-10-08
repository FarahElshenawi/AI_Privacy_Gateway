"""Tests for app/api — /detect, /mask, /demask endpoints (Role 2)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security.auth import get_install_token
from app.security import origin_check

# TestClient presents client host "testclient"; allow it so endpoint
# behavior (auth, pipeline) is testable. Origin rejection itself is
# covered in test_security.py.
origin_check.ALLOWED_HOSTS = origin_check.ALLOWED_HOSTS | {"testclient"}

client = TestClient(app, base_url="http://127.0.0.1:8765")
TOKEN = get_install_token()
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class TestHealthAndToken:
    def test_health_no_auth(self):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_token_endpoint_returns_install_token(self):
        res = client.get("/token")
        assert res.status_code == 200
        assert res.json()["token"] == TOKEN


class TestDetect:
    def test_missing_token_rejected(self):
        res = client.post("/api/detect", json={"text": "hi"})
        assert res.status_code == 401

    def test_wrong_token_rejected(self):
        res = client.post(
            "/api/detect",
            json={"text": "hi"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert res.status_code == 401

    def test_detect_credit_card(self):
        res = client.post(
            "/api/detect",
            json={"text": "my card is 4242 4242 4242 4242"},
            headers=AUTH,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["count"] >= 1
        assert isinstance(body["entities"], list)

    def test_detect_no_pii(self):
        res = client.post("/api/detect", json={"text": "hello world"}, headers=AUTH)
        assert res.status_code == 200
        assert res.json()["count"] == 0


class TestMaskAndDemask:
    def test_mask_redacts_credit_card(self):
        res = client.post(
            "/api/mask",
            json={"text": "card 4242 4242 4242 4242", "conversation_id": "t1"},
            headers=AUTH,
        )
        assert res.status_code == 200
        body = res.json()
        assert "4242 4242 4242 4242" not in body["masked_text"]
        assert "[REDACTED:" in body["masked_text"]
        assert body["safe_to_send"] is True
        assert body["entities_found"] >= 1

    def test_mask_demask_roundtrip_email(self):
        email = "farah@example.com"
        mask_res = client.post(
            "/api/mask",
            json={"text": f"email me at {email}", "conversation_id": "roundtrip"},
            headers=AUTH,
        )
        assert mask_res.status_code == 200
        masked = mask_res.json()["masked_text"]
        assert email not in masked

        demask_res = client.post(
            "/api/demask",
            json={"text": masked, "conversation_id": "roundtrip"},
            headers=AUTH,
        )
        assert demask_res.status_code == 200
        assert email in demask_res.json()["restored_text"]

    def test_demask_requires_auth(self):
        res = client.post("/api/demask", json={"text": "hi", "conversation_id": "x"})
        assert res.status_code == 401

    def test_demask_unknown_conversation_returns_text(self):
        res = client.post(
            "/api/demask",
            json={"text": "nothing to restore", "conversation_id": "nope"},
            headers=AUTH,
        )
        assert res.status_code == 200
        assert res.json()["restored_text"] == "nothing to restore"
        assert res.json()["replacements_made"] == 0
