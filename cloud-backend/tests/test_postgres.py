"""Runs only when TEST_POSTGRES_URL points at an EMPTY database (CI starts a postgres service).
Checks the real production path: Alembic builds the schema, the app refuses an unmigrated one,
and stats / time series / constraints behave on PostgreSQL."""
import os

import pytest
from fastapi.testclient import TestClient

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL not set")


def test_postgres_end_to_end(monkeypatch):
    from alembic import command
    from sqlalchemy import create_engine
    from app import db as dbm

    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("CLOUD_ADMIN_API_KEY", "a" * 32)
    monkeypatch.setenv("CLOUD_ENROLL_KEY", "e" * 32)
    dbm._engines.clear()
    eng = create_engine(URL)
    dbm.Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        dbm.init_db()
    dbm._engines.clear()
    command.upgrade(dbm._alembic_config(), "head")

    from app.main import create_app
    A, E = {"X-API-Key": "a" * 32}, {"X-Enroll-Key": "e" * 32}
    with TestClient(create_app()) as c:
        dev = c.post("/api/endpoints/enroll", json={"hostname": "pg-host"}, headers=E).json()
        H = {"X-Endpoint-Token": dev["endpoint_token"]}
        for lat in (10, 30):
            assert c.post("/api/audit", json={"event_type": "mask", "entity_types": {"EMAIL": 1},
                                               "entity_count": 1, "latency_ms": lat}, headers=H).status_code == 201
        st = c.get("/api/audit/stats", headers=A).json()
        assert st["total_events"] == 2 and st["avg_latency_ms"] == 20.0
        pts = c.get("/api/audit/timeseries?hours=3", headers=A).json()["points"]
        assert sum(p["events"] for p in pts) == 2
        assert c.post("/api/policies", json={"entity_type": "PGX", "action": "redact"}, headers=A).status_code == 201
        assert c.post("/api/policies", json={"entity_type": "PGX", "action": "faker"}, headers=A).status_code == 409
    dbm._engines.clear()
    dbm.Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
