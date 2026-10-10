import pytest

from conftest import KEY_A, sha256

# Every non-health route, with a valid-looking body where one is needed.
PROTECTED = [
    ("GET", "/api/policies", None),
    ("GET", "/api/policies/export", None),
    ("GET", "/api/policies/export/all", None),
    ("GET", "/api/policies/PERSON", None),
    ("POST", "/api/policies", {"entity_type": "X", "action": "keep"}),
    ("PUT", "/api/policies/1", {"action": "keep"}),
    ("DELETE", "/api/policies/1", None),
    ("GET", "/api/audit", None),
    ("GET", "/api/audit/stats", None),
    ("POST", "/api/audit", {"event_type": "mask"}),
    ("DELETE", "/api/audit/1", None),
    # Endpoints (local-backend fleet management)
    ("GET", "/api/endpoints", None),
    ("POST", "/api/endpoints/enroll", {"hostname": "h1"}),
    ("POST", "/api/endpoints/heartbeat", {}),
    ("GET", "/api/tenant-config", None),
    ("PUT", "/api/tenant-config", {"deny_terms": ["x1"]}),
    ("GET", "/api/admin/log", None),
    ("POST", "/api/admin/rotate-enroll-key", None),
    ("DELETE", "/api/endpoints/1", None),
]


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_every_route_rejects_a_missing_key(client, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 401, f"{method} {path} is not protected"


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_every_route_rejects_a_wrong_key(client, method, path, body):
    r = client.request(method, path, json=body, headers={"X-API-Key": "w" * 32})
    assert r.status_code == 401


def test_all_registered_api_routes_are_in_the_protected_list(client):
    """Guard: a new route added without auth (or without being listed here) fails this test."""
    import re
    schema = client.app.openapi()["paths"]
    routes = {(m.upper(), path) for path, ops in schema.items() if path.startswith("/api/")
              for m in ops if m.upper() not in ("HEAD", "OPTIONS")}
    covered = set()
    for m, p, _ in PROTECTED:
        for path in schema:
            pat = re.sub(r"\{[^}]+\}", "[^/]+", path)
            if path.startswith("/api/") and m.lower() in schema[path] and (re.fullmatch(pat, p) or path == p):
                covered.add((m, path))
    assert routes <= covered, f"unlisted routes: {routes - covered}"


def test_health_and_root_are_public(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/", follow_redirects=False).status_code in (302, 307)


def test_valid_key_via_x_api_key_and_bearer(client, A):
    assert client.get("/api/policies", headers=A).status_code == 200
    assert client.get("/api/policies", headers={"Authorization": f"Bearer {KEY_A}"}).status_code == 200


@pytest.mark.parametrize("headers", [{"X-API-Key": ""}, {"X-API-Key": "   "}, {"Authorization": "Bearer "},
                                     {"Authorization": f"Basic {KEY_A}"}, {"Authorization": KEY_A}])
def test_empty_or_malformed_credentials_rejected(client, headers):
    assert client.get("/api/policies", headers=headers).status_code == 401


def test_deactivated_org_key_is_rejected(client, A):
    from app.db import Organization, get_session, init_db
    s = get_session(init_db())
    s.query(Organization).filter(Organization.id == 1).update({"is_active": False})
    s.commit(); s.close()
    assert client.get("/api/policies", headers=A).status_code == 401


def test_only_a_hash_of_the_key_is_stored(client):
    from app.db import Organization, get_session, init_db
    s = get_session(init_db())
    stored = s.query(Organization).filter(Organization.id == 1).one().api_key
    s.close()
    assert stored == sha256(KEY_A) and stored != KEY_A


def test_generated_key_is_shown_once_and_works(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'g.db'}")
    monkeypatch.delenv("CLOUD_ADMIN_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app()) as c:
        err = capsys.readouterr().err
        key = next(l.strip() for l in err.splitlines() if len(l.strip()) >= 40 and " " not in l.strip() and "=" not in l)
        assert c.get("/api/policies", headers={"X-API-Key": key}).status_code == 200
    # restart: the key is NOT printed again and still works
    with TestClient(create_app()) as c:
        assert key not in capsys.readouterr().err
        assert c.get("/api/policies", headers={"X-API-Key": key}).status_code == 200


def test_setting_the_env_key_later_rotates_it(client, A, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    monkeypatch.setenv("CLOUD_ADMIN_API_KEY", "n" * 32)
    with TestClient(create_app()) as c2:
        assert c2.get("/api/policies", headers=A).status_code == 401           # old key dead
        assert c2.get("/api/policies", headers={"X-API-Key": "n" * 32}).status_code == 200


def test_weak_admin_key_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'w.db'}")
    monkeypatch.setenv("CLOUD_ADMIN_API_KEY", "short")
    from fastapi.testclient import TestClient
    from app.main import create_app
    with pytest.raises(RuntimeError):
        with TestClient(create_app()):
            pass


def test_cors_only_for_configured_dashboard_origins(client):
    pre = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-api-key"}
    evil = client.options("/api/policies", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in evil.headers
    ok = client.options("/api/policies", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-credentials" not in ok.headers


def test_database_url_is_honoured_and_engine_is_cached(client, tmp_path):
    from app.db import init_db
    assert init_db() is init_db()
    assert (tmp_path / "test.db").exists()
