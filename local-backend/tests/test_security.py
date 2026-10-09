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


# ── web pages must not be able to call the backend (token theft / demask) ──────────────

import stat
import sys

from fastapi.testclient import TestClient

from app.main import app

EXT = "chrome-extension://" + origin_check.PINNED_EXTENSION_ID


def _req(headers: dict[str, str], client_host: str = "127.0.0.1") -> Request:
    scope = {
        "type": "http", "method": "GET", "path": "/token", "client": (client_host, 1),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


class TestWebPagesAreRejected:
    @pytest.mark.parametrize("origin", [
        "https://chatgpt.com", "https://chat.openai.com", "https://gemini.google.com",
        "http://localhost:3000", "https://evil.example", "null",
    ])
    def test_web_origins_rejected(self, origin):
        with pytest.raises(HTTPException) as exc:
            origin_check.check_origin(_req({"Origin": origin, "Host": "127.0.0.1:8765"}))
        assert exc.value.status_code == 403

    def test_extension_origin_allowed(self):
        origin_check.check_origin(_req({"Origin": EXT, "Host": "127.0.0.1:8765"}))

    @pytest.mark.parametrize("origin", [
        "chrome-extension://abcdefghijklmnopabcdefghijklmnop",      # some OTHER extension
        "chrome-extension://" + "a" * 32,
        EXT + ".evil.com", EXT + "/",
    ])
    def test_other_extensions_are_rejected(self, origin):
        with pytest.raises(HTTPException) as exc:
            origin_check.check_origin(_req({"Origin": origin, "Host": "127.0.0.1:8765"}))
        assert exc.value.status_code == 403

    def test_extra_extension_ids_can_be_allowed_by_env(self, monkeypatch):
        other = "chrome-extension://" + "b" * 32
        monkeypatch.setenv("DLP_EXTENSION_IDS", "b" * 32 + ", not-an-id")
        origin_check.check_origin(_req({"Origin": other, "Host": "127.0.0.1:8765"}))
        assert "chrome-extension://not-an-id" not in origin_check.allowed_extension_origins()

    def test_cors_preflight_only_for_the_pinned_extension(self):
        c = TestClient(app, base_url="http://127.0.0.1:8765")
        for origin, ok in ((EXT, True), ("http://localhost:5173", False), ("http://localhost:3000", False),
                           ("chrome-extension://" + "c" * 32, False)):
            r = c.options("/api/mask", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
            assert (r.headers.get("access-control-allow-origin") == origin) is ok, origin

    def test_no_origin_allowed_for_non_browser_clients(self):
        origin_check.check_origin(_req({"Host": "127.0.0.1:8765"}))

    @pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "localhost", "[::1]:8765"])
    def test_loopback_host_headers_allowed(self, host):
        origin_check.check_origin(_req({"Host": host}))

    @pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8765", "127.0.0.1.evil.com:8765",
                                      "192.168.1.5:8765", "evil.com:80"])
    def test_dns_rebinding_host_rejected(self, host):
        # A rebound page is same-origin (so sends NO Origin header) but still sends the attacker's Host.
        with pytest.raises(HTTPException) as exc:
            origin_check.check_origin(_req({"Host": host}))
        assert exc.value.status_code == 403


class TestHttpLevel:
    @pytest.fixture(autouse=True)
    def _allow_testclient_peer(self, monkeypatch):
        monkeypatch.setattr(origin_check, "ALLOWED_HOSTS", origin_check.ALLOWED_HOSTS | {"testclient"})
        self.c = TestClient(app, base_url="http://127.0.0.1:8765")

    def test_page_cannot_read_token(self):
        r = self.c.get("/token", headers={"Origin": "https://chatgpt.com"})
        assert r.status_code == 403 and "token" not in r.json()

    def test_extension_can_read_token_and_call_api(self):
        tok = self.c.get("/token", headers={"Origin": EXT}).json()["token"]
        r = self.c.post("/api/detect", json={"text": "hello"},
                        headers={"Origin": EXT, "Authorization": f"Bearer {tok}"})
        assert r.status_code == 200

    def test_cors_preflight_denied_for_web_pages_allowed_for_extensions(self):
        pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
        web = self.c.options("/api/mask", headers={"Origin": "https://chatgpt.com", **pre})
        assert "access-control-allow-origin" not in web.headers
        ext = self.c.options("/api/mask", headers={"Origin": EXT, **pre})
        assert ext.headers.get("access-control-allow-origin") == EXT

    def test_rebound_host_rejected_on_every_endpoint(self):
        evil = TestClient(app, base_url="http://attacker.example:8765")
        assert evil.get("/token").status_code == 403


class TestTokenFile:
    def test_empty_bearer_never_authenticates(self):
        assert auth.verify_token("") is False

    def test_empty_token_in_file_is_replaced_not_trusted(self, tmp_path, monkeypatch):
        f = tmp_path / "t.json"
        f.write_text(json.dumps({"token": ""}))
        monkeypatch.setattr(auth, "_TOKEN_FILE", f)
        tok = auth.get_install_token()
        assert len(tok) >= 16 and auth.verify_token(tok) and not auth.verify_token("")

    def test_short_or_non_string_token_is_replaced(self, tmp_path, monkeypatch):
        f = tmp_path / "t.json"
        monkeypatch.setattr(auth, "_TOKEN_FILE", f)
        for bad in ({"token": "abc"}, {"token": 12345}, {"nottoken": 1}, []):
            f.write_text(json.dumps(bad))
            assert len(auth.get_install_token()) >= 16

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_new_token_file_is_private(self, tmp_path, monkeypatch):
        f = tmp_path / "t.json"
        monkeypatch.setattr(auth, "_TOKEN_FILE", f)
        auth.get_install_token()
        assert stat.S_IMODE(f.stat().st_mode) == 0o600

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_loose_existing_token_file_is_tightened(self, tmp_path, monkeypatch):
        f = tmp_path / "t.json"
        f.write_text(json.dumps({"token": "x" * 43}))
        f.chmod(0o644)
        monkeypatch.setattr(auth, "_TOKEN_FILE", f)
        auth.get_install_token()
        assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_pinned_extension_id_matches_the_manifest_key():
    """The backend only trusts PINNED_EXTENSION_ID; it must be the ID Chrome derives from the
    `key` in extension/manifest.json, or the extension would be locked out (or a different build trusted)."""
    import base64, hashlib, json
    from pathlib import Path
    manifest = json.loads((Path(__file__).resolve().parents[2] / "extension" / "manifest.json").read_text())
    der = base64.b64decode(manifest["key"])
    derived = "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])
    assert derived == origin_check.PINNED_EXTENSION_ID
