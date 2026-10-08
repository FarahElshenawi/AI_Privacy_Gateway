"""Storage policy, demasking, audit security, and BLOCK behavior tests for dlp_core."""
from __future__ import annotations

import logging
import re
import uuid

import pytest

from dlp_core import (
    Action,
    FernetSealer,
    InMemoryVault,
    MergedSpan,
    OffsetMasker,
    Demasker,
    Evidence,
)
from dlp_core.audit import AuditRecord, build_audit_record, emit_audit
from dlp_core.masker import RequestBlockedError
from dlp_core.policy import (
    ROUTING_TABLE,
    EntityCategory,
    ProcessingStrategy,
    RiskLevel,
    RoutingEntry,
    StoragePolicy,
    route_entity,
    route_label,
    should_block,
)

STORE_REDACT = ["IBAN", "ABA_ROUTING", "SWIFT_BIC", "INTERNAL_URL", "INTERNAL_HOSTNAME", "CRYPTO_WALLET"]
NO_STORE_REDACT = [
    "CREDIT_CARD", "CVV", "CARD_EXPIRY", "BANK_ACCOUNT_NUMBER", "US_SSN", "TAX_ID",
    "MEDICAL_RECORD_NUMBER", "HEALTH_INSURANCE_ID", "API_KEY", "AUTH_TOKEN", "JWT",
    "PRIVATE_KEY", "CLOUD_SECRET", "CONNECTION_STRING", "PASSWORD", "RECOVERY_CODE", "PEM_BLOCK",
]
MASK_TYPES = [
    "PERSON", "ORGANIZATION", "LOCATION", "EMAIL", "PHONE_NUMBER", "PHONE_E164",
    "ADDRESS", "USERNAME", "DATE_OF_BIRTH"
]
PLACEHOLDER_RE = re.compile(r"^\[\[REDACTED:([A-Z_0-9]+):[0-9a-f]{4}\]\]$")


def cid():
    return f"t_{uuid.uuid4().hex}"


def ent(t, text):
    return {"type": t, "text": text}


def span_helper(start: int, end: int, label: str, action: Action = Action.REDACT) -> MergedSpan:
    return MergedSpan(
        start=start,
        end=end,
        label=label,
        action=action,
        score=0.9,
        evidence=Evidence.VALIDATED,
        sources=("regex",),
        labels=(label,),
    )


# --- Storage policy table ---------------------------------------------------

@pytest.mark.parametrize("t", STORE_REDACT)
def test_store_for_demasking_redactions(t):
    d = route_entity(ent(t, "v"))
    assert (d.action, d.strategy, d.storage) == (
        Action.REDACT, ProcessingStrategy.REDACTION, StoragePolicy.STORE_FOR_DEMASKING
    )


@pytest.mark.parametrize("t", NO_STORE_REDACT)
def test_no_store_redactions(t):
    d = route_entity(ent(t, "v"))
    assert (d.action, d.strategy, d.storage) == (
        Action.REDACT, ProcessingStrategy.REDACTION, StoragePolicy.NO_STORE
    )


@pytest.mark.parametrize("t", MASK_TYPES)
def test_mask_is_pseudonymize_and_stored(t):
    d = route_entity(ent(t, "v"))
    assert (d.action, d.strategy, d.storage) == (
        Action.FAKER, ProcessingStrategy.PSEUDONYMIZE, StoragePolicy.STORE_FOR_DEMASKING
    )


def test_url_and_public_ip_keep_no_store():
    for e in (ent("URL", "https://a.com"), ent("IP_ADDRESS", "8.8.8.8")):
        d = route_entity(e)
        assert d.action is Action.KEEP and d.storage is StoragePolicy.NO_STORE


@pytest.mark.parametrize("e", [
    ent("IP_ADDRESS", "10.0.0.1"), ent("IPV4", "999.9.9.9"),
    ent("MYSTERY_TYPE", "x"), {"text": "x"}
])
def test_redacted_ips_and_unknown_are_no_store(e):
    d = route_entity(e)
    assert d.action is Action.REDACT and d.storage is StoragePolicy.NO_STORE


def test_default_storage_is_no_store():
    assert RoutingEntry(Action.REDACT, RiskLevel.HIGH, EntityCategory.PII).storage is StoragePolicy.NO_STORE


def test_storage_is_independent_of_action_and_strategy():
    a = RoutingEntry(Action.REDACT, RiskLevel.HIGH, EntityCategory.FINANCIAL, StoragePolicy.NO_STORE)
    b = RoutingEntry(Action.REDACT, RiskLevel.HIGH, EntityCategory.FINANCIAL, StoragePolicy.STORE_FOR_DEMASKING)
    assert a.action == b.action and a.strategy == b.strategy and a.storage != b.storage


# --- Masking behaviour: placeholders, vault, demasking ----------------------

def test_two_ibans_get_distinct_placeholders_and_both_demask():
    vault = InMemoryVault(FernetSealer())
    masker = OffsetMasker(vault)
    demasker = Demasker(vault)
    c = cid()

    iban1, iban2 = "DE89370400440532013000", "GB82WEST12345698765432"
    text = f"pay {iban1} then {iban2}"
    spans = [
        span_helper(4, 4 + len(iban1), "IBAN", Action.REDACT),
        span_helper(10 + len(iban1), 10 + len(iban1) + len(iban2), "IBAN", Action.REDACT),
    ]

    result = masker.mask(text, spans, c)
    masked = result.masked_text
    assert iban1 not in masked and iban2 not in masked

    p1 = masked[result.spans[0].masked_start:result.spans[0].masked_end]
    p2 = masked[result.spans[1].masked_start:result.spans[1].masked_end]
    assert p1 != p2
    assert PLACEHOLDER_RE.match(p1) and PLACEHOLDER_RE.match(p2)

    restored, count = demasker.restore(masked, c)
    assert restored == text
    assert count == 2


def test_same_stored_value_reuses_placeholder_across_calls():
    vault = InMemoryVault(FernetSealer())
    masker = OffsetMasker(vault)
    c = cid()
    iban = "DE89370400440532013000"

    res1 = masker.mask(iban, [span_helper(0, len(iban), "IBAN")], c)
    res2 = masker.mask(iban, [span_helper(0, len(iban), "IBAN")], c)
    assert res1.masked_text == res2.masked_text


@pytest.mark.parametrize("t", NO_STORE_REDACT)
def test_no_store_secrets_never_enter_vault_and_use_plain_placeholder(t):
    vault = InMemoryVault(FernetSealer())
    masker = OffsetMasker(vault)
    c = cid()
    secret = "SUPER-SECRET-" + t
    text = f"x {secret} y"
    spans = [span_helper(2, 2 + len(secret), t, Action.REDACT)]

    result = masker.mask(text, spans, c)
    assert result.masked_text == f"x [REDACTED:{t}] y"
    assert vault.items(c) == []


# --- Audit security ----------------------------------------------------------

def test_audit_record_has_only_metadata():
    d = route_entity(ent("API_KEY", "sk-secret"))
    r = build_audit_record(d, "conv1", 9)
    assert isinstance(r, AuditRecord)
    assert (r.entity_type, r.action, r.strategy, r.storage) == (
        "API_KEY", "REDACT", "REDACTION", "NO_STORE"
    )
    assert (r.risk_level, r.entity_category, r.rule) == ("CRITICAL", "CREDENTIAL", "policy:API_KEY")
    assert r.value_length == 9 and r.conversation_id == "conv1" and r.timestamp
    assert not any(f in r.__dataclass_fields__ for f in ("original_value", "value", "text", "hash"))


@pytest.mark.parametrize("t,secret", [
    ("API_KEY", "sk-live-ABCDEF123456"),
    ("IBAN", "DE89370400440532013000"),
    ("PERSON", "Zebediah Quillfeather"),
    ("CREDIT_CARD", "4111111111111111"),
])
def test_audit_log_never_contains_value_substring_hash_or_token(t, secret, caplog):
    vault = InMemoryVault(FernetSealer())
    masker = OffsetMasker(vault)
    c = cid()
    text = f"v {secret}"
    spans = [span_helper(2, 2 + len(secret), t, Action.REDACT if t != "PERSON" else Action.FAKER)]

    with caplog.at_level(logging.INFO, logger="privacy_gateway.audit"):
        res = masker.mask(text, spans, c)

    log = "\n".join(r.getMessage() for r in caplog.records)
    assert log, "an audit record must be emitted"
    assert f"entity_type={t}" in log and f"value_length={len(secret)}" in log
    assert secret not in log

    for i in range(len(secret) - 5):
        assert secret[i:i + 6] not in log

    rep = res.masked_text[res.spans[0].masked_start:res.spans[0].masked_end]
    assert rep not in log


# --- BLOCK action ------------------------------------------------------------

def test_block_raises_and_stops_processing():
    vault = InMemoryVault(FernetSealer())
    masker = OffsetMasker(vault)
    c = cid()

    text = "sensitive query with blocked token"
    spans = [span_helper(0, 9, "BLOCKED_TYPE", Action.BLOCK)]

    with pytest.raises(RequestBlockedError) as exc_info:
        masker.mask(text, spans, c)

    assert exc_info.value.entity_type == "BLOCKED_TYPE"
    assert vault.items(c) == []
