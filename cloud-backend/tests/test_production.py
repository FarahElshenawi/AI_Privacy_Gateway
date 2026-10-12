"""Alembic matches the models; constraints, policy history, SQL stats and the time series."""
from __future__ import annotations

from datetime import datetime, timedelta


def _post(client, hdr, **kw):
    body = {"event_type": "mask", "entity_types": {"EMAIL": 1}, "entity_count": 1, "latency_ms": 10}
    body.update(kw)
    r = client.post("/api/audit", json=body, headers=hdr)
    assert r.status_code == 201, r.text


def test_alembic_upgrade_matches_the_models(tmp_path, monkeypatch):
    """A database built only by migrations must have exactly the schema the models describe."""
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine
    from app.db import Base, _alembic_config

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(_alembic_config(), "head")
    eng = create_engine(url)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff
    command.downgrade(_alembic_config(), "base")


def test_non_sqlite_refuses_to_start_when_not_migrated(tmp_path, monkeypatch):
    import pytest
    from app import db as dbm
    monkeypatch.setattr(dbm, "create_engine", lambda url, **kw: __import__("sqlalchemy").create_engine(
        f"sqlite:///{tmp_path / 'x.db'}"))
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        dbm.init_db("postgresql://nobody@nowhere/none")


def test_one_policy_per_entity_type_is_enforced_by_the_database(client, A):
    from sqlalchemy.exc import IntegrityError
    from app.db import Policy, get_session, init_db
    s = get_session(init_db())
    s.add(Policy(org_id=1, entity_type="DUP_TYPE", action="redact"))
    s.commit()
    s.add(Policy(org_id=1, entity_type="DUP_TYPE", action="faker"))
    try:
        s.commit()
        raised = False
    except IntegrityError:
        s.rollback()
        raised = True
    finally:
        s.close()
    assert raised


def test_policy_history_records_create_update_rename_delete(client, A):
    p = client.post("/api/policies", json={"entity_type": "ZED", "action": "faker"}, headers=A).json()
    client.put(f"/api/policies/{p['id']}", json={"action": "redact"}, headers=A)
    client.put(f"/api/policies/{p['id']}", json={"entity_type": "ZED2"}, headers=A)
    client.delete(f"/api/policies/{p['id']}", headers=A)
    h = client.get("/api/policies/history", headers=A).json()
    seq = [(e["entity_type"], e["old_action"], e["new_action"]) for e in reversed(h)]
    assert seq == [("ZED", None, "faker"), ("ZED", "faker", "redact"), ("ZED", "redact", None),
                   ("ZED2", None, "redact"), ("ZED2", "redact", None)]
    assert all(e["changed_at"].endswith("Z") for e in h)


def test_policy_history_is_admin_only_and_org_scoped(client, A, B, device):
    client.post("/api/policies", json={"entity_type": "ONLY_A", "action": "redact"}, headers=A)
    assert client.get("/api/policies/history", headers=device[0]).status_code == 401
    assert client.get("/api/policies/history", headers=B).json() == []


def test_stats_aggregate_in_sql(client, A, device):
    hdr, _ = device
    _post(client, hdr, entity_types={"EMAIL": 2, "PERSON": 1}, entity_count=3, latency_ms=10)
    _post(client, hdr, entity_types={"EMAIL": 1}, entity_count=1, latency_ms=30)
    _post(client, hdr, event_type="fail_closed", entity_types={"POLICY_BLOCK": 1}, entity_count=1, latency_ms=None)
    st = client.get("/api/audit/stats", headers=A).json()
    assert st["total_events"] == 3 and st["fail_closed_events"] == 1
    assert st["avg_latency_ms"] == 20.0 and st["total_entities_masked"] == 4
    assert st["entity_type_breakdown"] == {"EMAIL": 3, "PERSON": 1}
    assert st["by_event_type"] == {"mask": 2, "fail_closed": 1}


def test_timeseries_buckets_fills_gaps_and_is_org_scoped(client, A, B, device):
    hdr, _ = device
    for _ in range(3):
        _post(client, hdr)
    _post(client, hdr, event_type="fail_closed", entity_types={"X": 1}, entity_count=1)
    ts = client.get("/api/audit/timeseries?hours=6", headers=A).json()
    pts = ts["points"]
    assert ts["bucket"] == "hour" and 6 <= len(pts) <= 8
    assert all(p["t"].endswith("Z") for p in pts)
    assert sum(p["events"] for p in pts) == 4 and sum(p["entities"] for p in pts) == 4
    last = max(pts, key=lambda p: p["events"])
    assert last["by_event_type"] == {"mask": 3, "fail_closed": 1}
    assert sum(p["events"] == 0 for p in pts) >= 4                       # gaps are zeros, axis is continuous
    assert sum(p["events"] for p in client.get("/api/audit/timeseries", headers=B).json()["points"]) == 0
    day = client.get("/api/audit/timeseries?hours=72&bucket=day", headers=A).json()
    assert day["bucket"] == "day" and sum(p["events"] for p in day["points"]) == 4
    assert client.get("/api/audit/timeseries?bucket=week", headers=A).status_code == 422
