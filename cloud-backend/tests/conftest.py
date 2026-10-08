import hashlib

import pytest
from fastapi.testclient import TestClient

KEY_A = "a" * 32
KEY_B = "b" * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh app + empty database per test. The default org's key is KEY_A."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("CLOUD_ADMIN_API_KEY", KEY_A)
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
        create_organization(s, "Other Co", KEY_B)
    finally:
        s.close()
    return {"X-API-Key": KEY_B}


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()
