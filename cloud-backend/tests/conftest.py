import hashlib

import pytest
from fastapi.testclient import TestClient

KEY_A = "a" * 32
KEY_B = "b" * 32
ENROLL_A = "e" * 32      # enrollment key of the default org
ENROLL_B = "f" * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh app + empty database per test. The default org's key is KEY_A."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("CLOUD_ADMIN_API_KEY", KEY_A)
    monkeypatch.setenv("CLOUD_ENROLL_KEY", ENROLL_A)
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def A():
    return {"X-API-Key": KEY_A}


@pytest.fixture
def B(client):
    """A second organization with its own key (isolation tests)."""
    from app.db import create_organization, get_session, init_db
    s = get_session(init_db())
    try:
        create_organization(s, "Other Co", KEY_B, ENROLL_B)
    finally:
        s.close()
    return {"X-API-Key": KEY_B}


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture
def E():
    """Enrollment-key header of the default org."""
    return {"X-Enroll-Key": ENROLL_A}


@pytest.fixture
def EB(B):
    return {"X-Enroll-Key": ENROLL_B}


@pytest.fixture
def device(client, E):
    """An enrolled device of the default org: returns (headers_with_endpoint_token, endpoint_id)."""
    r = client.post("/api/endpoints/enroll", json={"hostname": "dev-1", "version": "1.0.0"}, headers=E)
    assert r.status_code == 201, r.text
    return {"X-Endpoint-Token": r.json()["endpoint_token"]}, r.json()["endpoint_id"]
