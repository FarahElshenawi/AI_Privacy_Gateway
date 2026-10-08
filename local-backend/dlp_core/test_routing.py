"""Tests for the Policy & Deterministic Routing Engine migrated into dlp_core."""
from __future__ import annotations

import pytest

from dlp_core.policy import (
    Action,
    EntityCategory,
    PolicyAction,
    ProcessingStrategy,
    RiskLevel,
    ROUTING_TABLE,
    get_policy,
    get_routing_entry,
    is_publicly_routable_ip,
    normalize_label,
    normalize_type,
    route_entities,
    route_entity,
    route_label,
    should_block,
)


def ent(type_, text="x"):
    return {"type": type_, "text": text}


@pytest.mark.parametrize("t", ["PERSON", "ORGANIZATION", "LOCATION", "EMAIL", "PHONE_NUMBER",
                               "PHONE_E164", "ADDRESS", "USERNAME", "DATE_OF_BIRTH"])
def test_identity_pii_is_masked(t):
    d = route_entity(ent(t))
    assert d.action is Action.FAKER
    assert d.strategy is ProcessingStrategy.PSEUDONYMIZE
    assert d.entity_category is EntityCategory.PII


@pytest.mark.parametrize("t", [
    "CREDIT_CARD", "CVV", "CARD_EXPIRY", "IBAN", "SWIFT_BIC", "ABA_ROUTING",
    "BANK_ACCOUNT_NUMBER", "CRYPTO_WALLET", "US_SSN", "TAX_ID",
    "MEDICAL_RECORD_NUMBER", "HEALTH_INSURANCE_ID", "API_KEY", "AUTH_TOKEN", "JWT",
    "PRIVATE_KEY", "PEM_BLOCK", "CLOUD_SECRET", "CONNECTION_STRING", "PASSWORD",
    "RECOVERY_CODE", "INTERNAL_URL", "INTERNAL_HOSTNAME",
])
def test_secrets_and_identifiers_are_redacted(t):
    d = route_entity(ent(t))
    assert d.action is Action.REDACT
    assert d.strategy is ProcessingStrategy.REDACTION


def test_private_key_and_pem_block_are_redact_not_block():
    assert route_entity(ent("PRIVATE_KEY")).action is Action.REDACT
    assert route_entity(ent("PEM_BLOCK")).action is Action.REDACT
    # PEM is a generic container: lower risk than a confirmed private key
    assert route_entity(ent("PEM_BLOCK")).risk_level is RiskLevel.HIGH
    assert route_entity(ent("PRIVATE_KEY")).risk_level is RiskLevel.CRITICAL
    assert should_block([ent("PRIVATE_KEY"), ent("PEM_BLOCK")]) is False


def test_url_is_kept():
    d = route_entity(ent("URL", "https://example.com"))
    assert d.action is Action.KEEP and d.strategy is ProcessingStrategy.NONE


def test_unknown_type_fails_safe_to_redact():
    d = route_entity(ent("NOT_A_REAL_TYPE"))
    assert d.action is Action.REDACT
    assert d.risk_level is RiskLevel.UNKNOWN
    assert d.rule == "default:unknown-type"


def test_missing_type_fails_safe_to_redact():
    assert route_entity({"text": "x"}).action is Action.REDACT


@pytest.mark.parametrize("raw,canonical", [
    ("person", "PERSON"), (" Email ", "EMAIL"), ("SSN", "US_SSN"),
    ("card_number", "CREDIT_CARD"), ("access_token", "AUTH_TOKEN"),
    ("secret", "CLOUD_SECRET"), ("password", "PASSWORD"),
])
def test_case_and_alias_normalization(raw, canonical):
    assert normalize_type(raw) == canonical
    assert normalize_label(raw) == canonical
    assert route_entity(ent(raw)).label == canonical


def test_every_table_entry_is_uppercase_and_consistent():
    for k, p in ROUTING_TABLE.items():
        assert k == k.upper()
        assert get_routing_entry(k) is p
        assert get_policy(k) is p


def test_org_has_single_policy_regardless_of_value():
    for name in ["Google", "My Local Bakery", "ACME Internal Corp"]:
        assert route_entity(ent("ORGANIZATION", name)).action is Action.FAKER


# --- IP classification -------------------------------------------------------

@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34",
                                "2001:4860:4860::8888", "[2606:4700:4700::1111]"])
def test_global_ips_are_kept(ip):
    for t in ("IP_ADDRESS", "IPV4", "IPV6", "ip_address"):
        d = route_entity(ent(t, ip))
        assert d.action is Action.KEEP, (t, ip)


@pytest.mark.parametrize("ip", [
    "10.0.0.1", "172.16.5.4", "192.168.1.1",          # RFC 1918
    "127.0.0.1", "::1",                                # loopback
    "169.254.10.10", "fe80::1", "fe80::1%eth0",        # link-local
    "100.64.0.1",                                      # CGNAT
    "0.0.0.0", "::",                                   # unspecified
    "224.0.0.1", "ff02::1",                            # multicast
    "240.0.0.1", "255.255.255.255",                    # reserved / broadcast
    "192.0.2.1", "198.51.100.7", "203.0.113.9", "2001:db8::1",  # documentation
    "fc00::1", "fd12:3456::1",                         # ULA
    "::ffff:10.0.0.1", "::ffff:127.0.0.1",             # mapped private
    "64:ff9b::a00:1",                                  # NAT64 of 10.0.0.1
    "2002:0a00:0001::1",                               # 6to4 of 10.0.0.1
])
def test_non_global_ips_are_redacted(ip):
    d = route_entity(ent("IP_ADDRESS", ip))
    assert d.action is Action.REDACT, ip
    assert d.entity_category is EntityCategory.INFRASTRUCTURE


def test_mapped_public_ip_is_kept():
    assert route_entity(ent("IPV6", "::ffff:8.8.8.8")).action is Action.KEEP


@pytest.mark.parametrize("bad", ["999.1.1.1", "1.2.3", "", "   ", "8.8.8.8:53",
                                 "8.8.8.0/24", "not an ip", None, 12345])
def test_unparseable_or_ambiguous_ips_are_redacted(bad):
    assert route_entity({"type": "IP_ADDRESS", "text": bad}).action is Action.REDACT
    assert is_publicly_routable_ip(bad) is False


def test_ip_without_text_key_is_redacted():
    assert route_entity({"type": "IPV4"}).action is Action.REDACT


def test_type_only_lookup_for_ip_is_safe_default():
    assert get_policy("IP_ADDRESS").action is Action.REDACT


# --- determinism / batch -----------------------------------------------------

def test_decisions_are_deterministic_and_ordered():
    ents = [ent("PERSON", "A"), ent("IP_ADDRESS", "8.8.8.8"), ent("PASSWORD", "p"),
            ent("IP_ADDRESS", "10.1.1.1")]
    first = route_entities(ents)
    for _ in range(5):
        assert route_entities(ents) == first
    assert [d.action for d in first] == [Action.FAKER, Action.KEEP,
                                         Action.REDACT, Action.REDACT]


def test_decision_is_immutable():
    d = route_entity(ent("PERSON"))
    with pytest.raises(Exception):
        d.action = Action.KEEP  # type: ignore[misc]
