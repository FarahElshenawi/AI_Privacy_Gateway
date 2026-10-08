"""Unit tests for the policy configuration and overrides subsystem in dlp_core."""
from __future__ import annotations

import json
import pytest

from dlp_core.policy import (
    Action,
    EntityCategory,
    PolicyConfigError,
    RiskLevel,
    StoragePolicy,
    configure_policy_override,
    get_active_routing_table,
    get_routing_entry,
    load_policy_config,
    load_policy_config_file,
    reset_policy_to_defaults,
    route_entity,
    route_label,
)


@pytest.fixture(autouse=True)
def reset_policy_environment():
    """Ensure every test starts and ends with clean default policy table."""
    reset_policy_to_defaults()
    yield
    reset_policy_to_defaults()


def test_default_policy_unchanged():
    """Verify built-in baseline policies remain intact."""
    assert route_label("EMAIL").action is Action.FAKER
    assert route_label("PHONE_NUMBER").action is Action.FAKER
    assert route_label("IBAN").action is Action.REDACT
    assert route_label("IBAN").storage is StoragePolicy.STORE_FOR_DEMASKING
    assert route_label("API_KEY").action is Action.REDACT
    assert route_label("API_KEY").storage is StoragePolicy.NO_STORE
    assert route_label("PASSWORD").action is Action.REDACT
    assert route_label("URL").action is Action.KEEP


def test_configure_valid_override_action_only():
    """Configure overriding ORGANIZATION to REDACT."""
    assert route_label("ORGANIZATION").action is Action.FAKER
    configure_policy_override("ORGANIZATION", Action.REDACT)
    dec = route_label("ORGANIZATION")
    assert dec.action is Action.REDACT
    assert dec.strategy.value == "REDACTION"
    assert dec.rule == "override:ORGANIZATION"


def test_configure_valid_override_with_custom_storage_and_metadata():
    """Configure overriding SENSITIVE_DATE with specific metadata."""
    configure_policy_override(
        "SENSITIVE_DATE",
        "KEEP",
        risk_level="LOW",
        entity_category="PII",
        storage="NO_STORE",
    )
    dec = route_label("SENSITIVE_DATE")
    assert dec.action is Action.KEEP
    assert dec.risk_level is RiskLevel.LOW
    assert dec.entity_category is EntityCategory.PII
    assert dec.storage is StoragePolicy.NO_STORE


def test_override_new_unknown_entity_type():
    """Registering an override for an unknown custom entity type."""
    # Before override -> fails-safe to default:unknown-type REDACT
    before = route_label("PROJECT_CODENAME")
    assert before.action is Action.REDACT
    assert before.rule == "default:unknown-type"

    # Override it to MASK / FAKER
    configure_policy_override("PROJECT_CODENAME", "MASK", storage="STORE_FOR_DEMASKING")
    after = route_label("PROJECT_CODENAME")
    assert after.action is Action.FAKER
    assert after.storage is StoragePolicy.STORE_FOR_DEMASKING
    assert after.rule == "override:PROJECT_CODENAME"


def test_action_only_override_preserves_existing_metadata():
    """Requirement 2: Overriding action only preserves existing risk, category, and storage."""
    # Check baseline IBAN: action=REDACT, risk=HIGH, category=FINANCIAL, storage=STORE_FOR_DEMASKING
    orig_iban = route_label("IBAN")
    assert orig_iban.action is Action.REDACT
    assert orig_iban.risk_level is RiskLevel.HIGH
    assert orig_iban.entity_category is EntityCategory.FINANCIAL
    assert orig_iban.storage is StoragePolicy.STORE_FOR_DEMASKING

    # Override IBAN to BLOCK without passing risk, category, or storage
    configure_policy_override("IBAN", Action.BLOCK)
    overridden_iban = route_label("IBAN")
    assert overridden_iban.action is Action.BLOCK
    assert overridden_iban.risk_level is RiskLevel.HIGH
    assert overridden_iban.entity_category is EntityCategory.FINANCIAL
    assert overridden_iban.storage is StoragePolicy.STORE_FOR_DEMASKING

    # Override PERSON to REDACT without passing metadata: preserves PII and STORE_FOR_DEMASKING
    configure_policy_override("PERSON", Action.REDACT)
    overridden_person = route_label("PERSON")
    assert overridden_person.action is Action.REDACT
    assert overridden_person.risk_level is RiskLevel.HIGH
    assert overridden_person.entity_category is EntityCategory.PII
    assert overridden_person.storage is StoragePolicy.STORE_FOR_DEMASKING


def test_security_prevents_critical_secrets_and_gov_ids_from_being_downgraded_to_keep():
    """Security rule: Credentials, PCI, Gov IDs, and Health IDs cannot be overridden to KEEP."""
    critical_entities = [
        "API_KEY", "AUTH_TOKEN", "JWT", "PRIVATE_KEY", "CLOUD_SECRET",
        "CONNECTION_STRING", "PASSWORD", "RECOVERY_CODE", "CREDIT_CARD", "CVV",
        "US_SSN", "TAX_ID", "MEDICAL_RECORD_NUMBER", "HEALTH_INSURANCE_ID",
        "GOVERNMENT_ID", "PASSPORT_NUMBER", "DRIVERS_LICENSE_NUMBER",
    ]
    for ent_type in critical_entities:
        with pytest.raises(PolicyConfigError, match="Security violation"):
            configure_policy_override(ent_type, Action.KEEP)

        with pytest.raises(PolicyConfigError, match="Security violation"):
            configure_policy_override(ent_type, "KEEP")

        # Verify it remained Action.REDACT
        assert route_label(ent_type).action is Action.REDACT


def test_invalid_action_raises_policy_config_error():
    with pytest.raises(PolicyConfigError, match="Unknown policy action"):
        configure_policy_override("ORGANIZATION", "NOT_A_REAL_ACTION")


def test_invalid_metadata_enums_raise_policy_config_error():
    with pytest.raises(PolicyConfigError, match="Unknown risk level"):
        configure_policy_override("ORGANIZATION", "REDACT", risk_level="ULTRA_HIGH")

    with pytest.raises(PolicyConfigError, match="Unknown entity category"):
        configure_policy_override("ORGANIZATION", "REDACT", entity_category="ALIEN")

    with pytest.raises(PolicyConfigError, match="Unknown storage policy"):
        configure_policy_override("ORGANIZATION", "REDACT", storage="MAYBE")


def test_load_policy_config_dict():
    config = {
        "OVERRIDES": {
            "ORGANIZATION": "REDACT",
            "LOCATION": {"action": "BLOCK"},
            "INTERNAL_URL": {"action": "REDACT", "storage": "NO_STORE"},
        }
    }
    applied = load_policy_config(config)
    assert applied == 3

    assert route_label("ORGANIZATION").action is Action.REDACT
    assert route_label("LOCATION").action is Action.BLOCK
    assert route_label("INTERNAL_URL").storage is StoragePolicy.NO_STORE


def test_load_policy_config_file(tmp_path):
    config_file = tmp_path / "custom_policy.json"
    config_data = {
        "OVERRIDES": {
            "ORGANIZATION": "REDACT",
            "CUSTOM_TOKEN": {"action": "MASK", "storage": "STORE_FOR_DEMASKING", "risk_level": "HIGH"}
        }
    }
    config_file.write_text(json.dumps(config_data), encoding="utf-8")

    applied = load_policy_config_file(str(config_file))
    assert applied == 2

    assert route_label("ORGANIZATION").action is Action.REDACT
    assert route_label("CUSTOM_TOKEN").action is Action.FAKER
    assert route_label("CUSTOM_TOKEN").storage is StoragePolicy.STORE_FOR_DEMASKING


def test_reset_policy_to_defaults():
    configure_policy_override("ORGANIZATION", "REDACT")
    assert route_label("ORGANIZATION").action is Action.REDACT

    reset_policy_to_defaults()
    assert route_label("ORGANIZATION").action is Action.FAKER


def test_complete_routing_table_default_policy_unchanged_after_overrides_and_reset():
    """Requirement 4: Verify the entire ROUTING_TABLE matches default policy after overrides and reset."""
    from dlp_core.policy import ROUTING_TABLE

    # 1. Take a snapshot of every single decision across all known types
    baseline_decisions = {k: route_label(k) for k in ROUTING_TABLE}

    # 2. Apply multiple extensive overrides across different entities
    configure_policy_override("ORGANIZATION", "REDACT")
    configure_policy_override("EMAIL", "BLOCK")
    configure_policy_override("INTERNAL_URL", "REDACT", storage="NO_STORE")
    configure_policy_override("CUSTOM_NEW_TYPE", "MASK")

    # Verify mutations took effect
    assert route_label("ORGANIZATION").action is Action.REDACT
    assert route_label("EMAIL").action is Action.BLOCK
    assert route_label("INTERNAL_URL").storage is StoragePolicy.NO_STORE
    assert route_label("CUSTOM_NEW_TYPE").action is Action.FAKER

    # 3. Reset back to defaults
    reset_policy_to_defaults()

    # 4. Assert every single entry in ROUTING_TABLE matches the original baseline exactly
    for label, default_entry in ROUTING_TABLE.items():
        restored = route_label(label)
        baseline = baseline_decisions[label]
        assert restored.label == baseline.label
        assert restored.action == baseline.action
        assert restored.strategy == baseline.strategy
        assert restored.risk_level == baseline.risk_level
        assert restored.entity_category == baseline.entity_category
        assert restored.storage == baseline.storage
        assert restored.rule == f"policy:{label}"
