"""One organization must never see or change another's data."""


def _policy_id(client, headers, etype):
    return next(p["id"] for p in client.get("/api/policies", headers=headers).json() if p["entity_type"] == etype)


def test_orgs_see_only_their_own_policies(client, A, B):
    assert len(client.get("/api/policies", headers=A).json()) == 15
    assert client.get("/api/policies", headers=B).json() == []
    assert client.get("/api/policies/PERSON", headers=B).status_code == 404
    assert client.get("/api/policies/export", headers=B).json() == {}


def test_org_id_query_param_cannot_switch_tenants(client, A, B):
    assert client.get("/api/policies?org_id=1", headers=B).json() == []
    assert len(client.get("/api/policies?org_id=2", headers=A).json()) == 15


def test_cannot_update_or_delete_another_orgs_policy(client, A, B):
    pid = _policy_id(client, A, "PERSON")
    assert client.put(f"/api/policies/{pid}", json={"action": "keep"}, headers=B).status_code == 404
    assert client.delete(f"/api/policies/{pid}", headers=B).status_code == 404
    assert client.get("/api/policies/PERSON", headers=A).json()["action"] == "faker"


def test_body_org_id_is_ignored_on_create(client, A, B):
    r = client.post("/api/policies", json={"org_id": 1, "entity_type": "NEWTYPE", "action": "redact"}, headers=B)
    assert r.status_code == 201 and r.json()["org_id"] != 1
    assert "NEWTYPE" not in client.get("/api/policies/export", headers=A).json()
    assert client.get("/api/policies/export", headers=B).json() == {"NEWTYPE": "redact"}


def test_same_entity_type_can_exist_in_two_orgs(client, A, B):
    assert client.post("/api/policies", json={"entity_type": "PERSON", "action": "redact"}, headers=B).status_code == 201


def test_audit_events_are_isolated(client, A, B):
    ev = client.post("/api/audit", json={"event_type": "mask", "entity_count": 2,
                                         "entity_types": {"PERSON": 2}}, headers=A).json()
    assert [e["id"] for e in client.get("/api/audit", headers=A).json()] == [ev["id"]]
    assert client.get("/api/audit", headers=B).json() == []
    assert client.get("/api/audit/stats", headers=B).json()["total_events"] == 0
    assert client.get("/api/audit/stats", headers=A).json()["total_entities_masked"] == 2
    assert client.delete(f"/api/audit/{ev['id']}", headers=B).status_code == 404
    assert client.delete(f"/api/audit/{ev['id']}", headers=A).status_code == 204


def test_endpoint_id_must_belong_to_the_caller(client, A, B):
    from app.db import Endpoint, get_session, init_db
    s = get_session(init_db()); ep = Endpoint(org_id=1, hostname="h"); s.add(ep); s.commit(); eid = ep.id; s.close()
    assert client.post("/api/audit", json={"event_type": "mask", "endpoint_id": eid}, headers=A).status_code == 201
    assert client.post("/api/audit", json={"event_type": "mask", "endpoint_id": eid}, headers=B).status_code == 422
