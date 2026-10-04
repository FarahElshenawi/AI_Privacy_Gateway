"""Tests for app/security — per-install token auth and origin check (Role 2)."""
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.security import auth, origin_check


def _make_request(client_host: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/mask",
        "headers": [],
        "client": (client_host, 12345),
    }
    return Request(scope)


class TestTokenAuth:
    def test_verify_none_token_fails_closed(self):
        assert auth.verify_token(None) is False

    def test_verify_wrong_token_rejected(self):
        assert auth.verify_token("definitely-not-the-token") is False

    def test_verify_correct_token_accepted(self):
        token = auth.get_install_token()
        assert auth.verify_token(token) is True

    def test_token_is_stable_across_calls(self):
        assert auth.get_install_token() == auth.get_install_token()

    def test_token_file_created_and_valid_json(self):
        token = auth.get_install_token()
        data = json.loads(auth._TOKEN_FILE.read_text())
        assert data["token"] == token

    def test_new_token_generated_when_file_missing(self, tmp_path, monkeypatch):
        fake = tmp_path / "token.json"
        monkeypatch.setattr(auth, "_TOKEN_FILE", fake)
        token = auth.get_install_token()
        assert token and fake.exists()
        assert auth.verify_token(token) is True

    def test_corrupt_token_file_regenerates(self, tmp_path, monkeypatch):
        fake = tmp_path / "token.json"
        fake.write_text("not json{{{")
        monkeypatch.setattr(auth, "_TOKEN_FILE", fake)
        token = auth.get_install_token()
        assert token and auth.verify_token(token) is True


class TestOriginCheck:
    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
    def test_local_hosts_allowed(self, host):
        origin_check.check_origin(_make_request(host))  # no exception

    @pytest.mark.parametrize("host", ["8.8.8.8", "192.168.1.5", "evil.example.com", ""])
    def test_remote_hosts_rejected(self, host):
        with pytest.raises(HTTPException) as exc:
            origin_check.check_origin(_make_request(host))
        assert exc.value.status_code == 403

    def test_missing_client_rejected(self):
        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": None}
        with pytest.raises(HTTPException) as exc:
            origin_check.check_origin(Request(scope))
        assert exc.value.status_code == 403
