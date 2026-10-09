import pytest
from app.defaults import CANONICAL_DEFAULT_POLICIES


def test_export_route_is_not_captured_as_an_entity_type(client, A):
    """Regression: the dashboard calls /api/policies/export, which used to hit /{entity_type} -> 404."""
    r = client.get("/api/policies/export", headers=A)
    assert r.status_code == 200 and r.json()["PERSON"] == "faker" and len(r.json()) == len(CANONICAL_DEFAULT_POLICIES)
    assert client.get("/api/policies/export/all", headers=A).json() == r.json()


@pytest.mark.parametrize("body", [
    {"entity_type": "X", "action": "delete"}, {"entity_type": "X", "action": ""}, {"entity_type": "X"},
    {"entity_type": "", "action": "keep"}, {"entity_type": "has space", "action": "keep"},
    {"entity_type": "9LEADING", "action": "keep"}, {"entity_type": "A" * 65, "action": "keep"},
    {"entity_type": "<script>", "action": "keep"},
])
def test_invalid_policies_rejected(client, A, body):
    assert client.post("/api/policies", json=body, headers=A).status_code == 422


def test_entity_type_is_normalised_to_upper_case(client, A):
    r = client.post("/api/policies", json={"entity_type": " nickname ", "action": "redact"}, headers=A)
    assert r.status_code == 201 and r.json()["entity_type"] == "NICKNAME"
    assert client.get("/api/policies/nickname", headers=A).json()["action"] == "redact"


def test_duplicate_and_rename_conflicts(client, A):
    assert client.post("/api/policies", json={"entity_type": "person", "action": "keep"}, headers=A).status_code == 409
    pid = client.get("/api/policies/EMAIL", headers=A).json()["id"]
    assert client.put(f"/api/policies/{pid}", json={"entity_type": "PERSON"}, headers=A).status_code == 409


def test_update_bumps_version_only_when_action_changes(client, A):
    p = client.get("/api/policies/PERSON", headers=A).json()
    same = client.put(f"/api/policies/{p['id']}", json={"action": "faker"}, headers=A).json()
    assert same["version"] == p["version"]
    changed = client.put(f"/api/policies/{p['id']}", json={"action": "redact"}, headers=A).json()
    assert changed["version"] == p["version"] + 1 and changed["action"] == "redact"
    assert client.put(f"/api/policies/{p['id']}", json={"action": "bogus"}, headers=A).status_code == 422


def test_default_policies_cannot_be_deleted_but_custom_ones_can(client, A):
    pid = client.get("/api/policies/PERSON", headers=A).json()["id"]
    assert client.delete(f"/api/policies/{pid}", headers=A).status_code == 400
    new = client.post("/api/policies", json={"entity_type": "TMP", "action": "keep"}, headers=A).json()
    assert client.delete(f"/api/policies/{new['id']}", headers=A).status_code == 204


# ── audit: "metadata only" is structural ──

@pytest.mark.parametrize("extra", [{"text": "my SSN is 123"}, {"prompt": "x"}, {"masked_text": "x"}, {"value": "x"},
                                   {"org_id": 5}])
def test_audit_rejects_any_field_that_could_carry_content(client, A, extra):
    assert client.post("/api/audit", json={"event_type": "mask", **extra}, headers=A).status_code == 422


@pytest.mark.parametrize("body", [
    {"event_type": "other"},
    {"event_type": "mask", "entity_types": {"PERSON": -1}},
    {"event_type": "mask", "entity_types": {"PERSON": "John Smith"}},          # a value where a count belongs
    {"event_type": "mask", "entity_types": {"john smith": 1}},                  # a name where a label belongs
    {"event_type": "mask", "entity_types": {f"T{i}": 1 for i in range(65)}},
    {"event_type": "mask", "entity_count": -1},
    {"event_type": "mask", "latency_ms": -5},
    {"event_type": "mask", "conversation_id": "has spaces and *"},
    {"event_type": "mask", "conversation_id": "c" * 65},
])
def test_invalid_audit_events_rejected(client, A, body):
    assert client.post("/api/audit", json=body, headers=A).status_code == 422


def test_valid_audit_event_roundtrip(client, A):
    body = {"event_type": "mask", "entity_types": {"PERSON": 3, "EMAIL": 1}, "entity_count": 4,
            "latency_ms": 120, "conversation_id": "conv_68f1a2b3-1111-2222-3333-444455556666"}
    r = client.post("/api/audit", json=body, headers=A)
    assert r.status_code == 201
    stats = client.get("/api/audit/stats", headers=A).json()
    assert stats["entity_type_breakdown"] == {"PERSON": 3, "EMAIL": 1} and stats["avg_latency_ms"] == 120.0
    assert client.get("/api/audit?event_type=file", headers=A).json() == []


def test_demask_event_type_is_accepted_and_not_counted_as_mask(client, A):
    r = client.post("/api/audit", headers=A, json={"event_type": "demask", "entity_count": 4})
    assert r.status_code == 201
    stats = client.get("/api/audit/stats", headers=A).json()
    assert stats["by_event_type"].get("demask") == 1
    assert stats["total_entities_masked"] == 0


def test_audit_timestamps_are_utc_with_a_zone_marker(client, A):
    r = client.post("/api/audit", headers=A, json={"event_type": "mask", "entity_count": 1})
    assert r.status_code == 201
    assert r.json()["timestamp"].endswith("Z")
    assert client.get("/api/audit", headers=A).json()[0]["timestamp"].endswith("Z")
