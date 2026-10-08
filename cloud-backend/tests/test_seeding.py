"""Tests for cloud-backend policy catalog and seed_defaults behavior."""
import pytest
from app.db import (
    Organization,
    Policy,
    create_organization,
    get_session,
    init_db,
    seed_defaults,
)
from app.defaults import CANONICAL_DEFAULT_POLICIES


def test_canonical_catalog_size_and_types():
    # Exactly 39 canonical entities
    assert len(CANONICAL_DEFAULT_POLICIES) == 39

    # No aliases
    for alias in ["SSN", "SECRET", "ACCESS_TOKEN", "CARD_NUMBER"]:
        assert alias not in CANONICAL_DEFAULT_POLICIES

    # No static IP labels
    for ip_label in ["IPV4", "IPV6", "IP_ADDRESS"]:
        assert ip_label not in CANONICAL_DEFAULT_POLICIES

    # Canonical types present
    assert CANONICAL_DEFAULT_POLICIES["US_SSN"] == "redact"
    assert CANONICAL_DEFAULT_POLICIES["CLOUD_SECRET"] == "redact"
    assert CANONICAL_DEFAULT_POLICIES["PERSON"] == "faker"
    assert CANONICAL_DEFAULT_POLICIES["URL"] == "keep"


def test_seed_defaults_seeds_all_canonical_entities(client, A):
    policies = client.get("/api/policies", headers=A).json()
    entity_types = {p["entity_type"] for p in policies}

    assert len(policies) == len(CANONICAL_DEFAULT_POLICIES)
    assert entity_types == set(CANONICAL_DEFAULT_POLICIES.keys())

    # Verify no aliases or static IPs
    for forbidden in ["SSN", "SECRET", "ACCESS_TOKEN", "CARD_NUMBER", "IPV4", "IPV6", "IP_ADDRESS"]:
        assert forbidden not in entity_types


def test_seed_defaults_is_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'idempotent.db'}"
    engine = init_db(db_url)

    # First run
    seed_defaults(engine)
    session = get_session(engine)
    count1 = session.query(Policy).count()
    session.close()

    # Second run
    seed_defaults(engine)
    session = get_session(engine)
    count2 = session.query(Policy).count()
    session.close()

    assert count1 == len(CANONICAL_DEFAULT_POLICIES)
    assert count2 == count1


def test_seed_defaults_preserves_customized_policies(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'customized.db'}"
    engine = init_db(db_url)

    # First seed
    seed_defaults(engine)

    # Administrator customizes PERSON to 'redact' and bumps version
    session = get_session(engine)
    person = session.query(Policy).filter(Policy.entity_type == "PERSON").first()
    assert person is not None
    person.action = "redact"
    person.version = 5
    session.commit()
    session.close()

    # Second seed run (e.g. application restart)
    seed_defaults(engine)

    # Verify PERSON remained 'redact' with version 5, not reset to 'faker'
    session = get_session(engine)
    person_after = session.query(Policy).filter(Policy.entity_type == "PERSON").first()
    assert person_after.action == "redact"
    assert person_after.version == 5
    session.close()


def test_seed_defaults_migrates_legacy_ssn_and_cleans_static_ips(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = init_db(db_url)

    # Manually simulate a legacy database with SSN (action='block'), IPV4, IPV6
    session = get_session(engine)
    org, _ = create_organization(session, "Legacy Org", "x" * 32)
    session.add(Policy(org_id=org.id, entity_type="SSN", action="block", version=3, is_default=True))
    session.add(Policy(org_id=org.id, entity_type="IPV4", action="keep", is_default=True))
    session.add(Policy(org_id=org.id, entity_type="IPV6", action="keep", is_default=True))
    session.commit()
    session.close()

    # Run seed_defaults to migrate
    seed_defaults(engine)

    session = get_session(engine)
    # SSN should be migrated to US_SSN preserving action ('block') and version (3)
    us_ssn = session.query(Policy).filter(Policy.entity_type == "US_SSN").first()
    assert us_ssn is not None
    assert us_ssn.action == "block"
    assert us_ssn.version == 3

    # Legacy SSN row should be gone
    assert session.query(Policy).filter(Policy.entity_type == "SSN").first() is None

    # Legacy static IP rows should be cleaned up
    assert session.query(Policy).filter(Policy.entity_type == "IPV4").first() is None
    assert session.query(Policy).filter(Policy.entity_type == "IPV6").first() is None

    # All canonical policies should now be present
    total = session.query(Policy).count()
    assert total == len(CANONICAL_DEFAULT_POLICIES)
    session.close()
