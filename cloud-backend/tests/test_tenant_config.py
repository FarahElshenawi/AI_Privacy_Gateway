"""Tenant config: admin writes, devices read, terms never reach the admin log."""
from __future__ import annotations


def test_defaults_are_empty_and_version_zero(client, A):
    assert client.get("/api/tenant-config", headers=A).json() == {
        "deny_terms": [], "tenant_domains": [], "image_policy": "default", "version": 0}


def test_admin_sets_and_device_reads(client, A, device):
    body = {"deny_terms": ["Project Falcon", "project falcon", " Acme Corp "],
            "tenant_domains": ["*.Corp.Acme.com", ".corp.acme.com", "intra"]}
    r = client.put("/api/tenant-config", json=body, headers=A)
    assert r.status_code == 200
    assert r.json()["deny_terms"] == ["Project Falcon", "Acme Corp"]           # trimmed, case-insensitive dedupe
    assert r.json()["tenant_domains"] == ["corp.acme.com", "intra"] and r.json()["version"] == 1
    hdr, _ = device
    got = client.get("/api/tenant-config", headers=hdr).json()
    assert got["deny_terms"] == ["Project Falcon", "Acme Corp"] and got["version"] == 1
    assert client.put("/api/tenant-config", json=body, headers=A).json()["version"] == 2


def test_device_cannot_write(client, device):
    hdr, _ = device
    assert client.put("/api/tenant-config", json={"deny_terms": ["abc"]}, headers=hdr).status_code == 401


def test_validation(client, A):
    for bad in ({"deny_terms": ["x"]}, {"deny_terms": ["ok\x00term"]}, {"deny_terms": ["y" * 101]},
                {"tenant_domains": ["bad domain"]}, {"tenant_domains": ["http://x.com"]},
                {"deny_terms": ["aa"] * 0 + [f"term{i}" for i in range(501)]}):
        assert client.put("/api/tenant-config", json=bad, headers=A).status_code == 422, bad


def test_config_is_org_scoped_and_terms_are_not_logged(client, A, B):
    client.put("/api/tenant-config", json={"deny_terms": ["Secret Client Name"]}, headers=A)
    assert client.get("/api/tenant-config", headers=B).json()["deny_terms"] == []
    log = client.get("/api/admin/log", headers=A).json()
    assert any(e["action"] == "tenant_config.update" for e in log)
    assert "Secret Client Name" not in str(log)


def test_image_policy_roundtrip_and_validation(client, A, device):
    assert client.put("/api/tenant-config", json={"image_policy": "block"}, headers=A).json()["image_policy"] == "block"
    hdr, _ = device
    assert client.get("/api/tenant-config", headers=hdr).json()["image_policy"] == "block"
    assert client.put("/api/tenant-config", json={"image_policy": "ignore"}, headers=A).status_code == 422
    assert any("images=block" in str(e) for e in client.get("/api/admin/log", headers=A).json())
