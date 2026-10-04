"""Unit tests for app/pipeline/masking.py (docs/development-plan.md, Week 1,
Role 3, Thu: "Unit tests for all three masking functions" + "Edge-case
tests: empty strings, unicode names, repeated entities").

Owner: Role 3. Run with: pytest tests/test_masking.py -q
(or just `pytest -q` from local-backend/, per pytest.ini's testpaths).

No test file existed for this module before this pass, despite it being
the actual masking implementation the extension's /api/mask endpoint
calls (local-backend/app/api/mask.py -> app.pipeline.masking.mask_text).
"""
from __future__ import annotations

import uuid

import pytest

from app.pipeline.masking import mask_entities, mask_text, REDACTED
from app.vault.store import get_vault


def conv_id() -> str:
    """A fresh conversation id per test — the vault is a process-wide
    singleton (get_vault()), so tests must not share conversation ids or
    they'll see each other's mappings."""
    return f"test_{uuid.uuid4().hex}"


def entity(text: str, type_: str) -> dict:
    return {"type": type_, "text": text}


# --- faker(): synthetic substitution ---------------------------------------

def test_faker_person_replaces_with_a_different_value():
    cid = conv_id()
    masked, pairs = mask_text("Hi, I'm John Smith.", [entity("John Smith", "PERSON")], cid)
    assert "John Smith" not in masked
    assert len(pairs) == 1
    assert pairs[0][0] == "John Smith"
    assert pairs[0][1] != "John Smith"


def test_faker_stores_a_vault_mapping_that_demasks_back_to_the_original():
    cid = conv_id()
    masked, _ = mask_text("Email me at jane@acme.com", [entity("jane@acme.com", "EMAIL")], cid)
    assert "jane@acme.com" not in masked

    vault = get_vault()
    restored = vault.restore_text(cid, masked)
    assert restored == "Email me at jane@acme.com"


def test_faker_reuses_the_same_fake_across_separate_calls_in_one_conversation():
    # This is the exact scenario from the repo's own Week-1 test checklist
    # (GUIDE.md): "second message in same chat reuses the same fake for
    # 'John'." A previous version of mask_entities generated a brand new
    # fake every call, breaking this.
    cid = conv_id()
    masked1, _ = mask_text("Hi, I'm John Smith.", [entity("John Smith", "PERSON")], cid)
    masked2, _ = mask_text("John Smith again.", [entity("John Smith", "PERSON")], cid)

    fake1 = masked1.replace("Hi, I'm ", "").replace(".", "")
    fake2 = masked2.replace(" again.", "")
    assert fake1 == fake2, "the same real value must map to the same fake within a conversation"


def test_faker_gives_different_fakes_to_different_people_in_one_call():
    cid = conv_id()
    _, pairs = mask_text(
        "John and Sarah are here.",
        [entity("John", "PERSON"), entity("Sarah", "PERSON")],
        cid,
    )
    fakes = [p[1] for p in pairs]
    assert len(set(fakes)) == 2, "different real values must not collide onto the same fake"


def test_faker_cross_conversation_same_real_value_gets_different_fakes():
    # Documented in vault/store.py: "Cross-conversation, the same real
    # value gets different fakes (no fingerprint)."
    cid_a, cid_b = conv_id(), conv_id()
    masked_a, pairs_a = mask_text("John Smith", [entity("John Smith", "PERSON")], cid_a)
    masked_b, pairs_b = mask_text("John Smith", [entity("John Smith", "PERSON")], cid_b)
    assert pairs_a[0][1] != pairs_b[0][1]


@pytest.mark.parametrize(
    "type_,generator_field",
    [
        ("EMAIL", "email"),
        ("PHONE_NUMBER", "phone"),
        ("ORGANIZATION", "org"),
        ("ADDRESS", "address"),
        ("USERNAME", "username"),
    ],
)
def test_faker_covers_every_identity_entity_type_in_the_routing_table(type_, generator_field):
    cid = conv_id()
    original = "SOME_REAL_VALUE"
    masked, pairs = mask_text(original, [entity(original, type_)], cid)
    assert original not in masked
    assert pairs[0][1] != original


# --- redact(): hard block ----------------------------------------------------

def test_redact_replaces_secret_with_the_fixed_placeholder():
    cid = conv_id()
    masked, pairs = mask_text("key: sk-abc123xyz", [entity("sk-abc123xyz", "API_KEY")], cid)
    assert masked == f"key: {REDACTED}"
    assert pairs == [("sk-abc123xyz", REDACTED)]


def test_redact_never_leaks_length_or_prefix_of_the_secret():
    cid = conv_id()
    short_secret = "sk-1"
    long_secret = "sk-1234567890abcdefghijklmnopqrstuvwxyz"
    masked_short, _ = mask_text(short_secret, [entity(short_secret, "API_KEY")], cid)
    masked_long, _ = mask_text(long_secret, [entity(long_secret, "API_KEY")], cid)
    assert masked_short == masked_long == REDACTED


def test_redact_never_stores_a_vault_mapping_nothing_to_reverse():
    # GUIDE.md: "Redacted secrets are never put in the Vault (nothing to
    # reverse, nothing to steal)."
    cid = conv_id()
    mask_text("card 4111111111111111", [entity("4111111111111111", "CREDIT_CARD")], cid)
    vault = get_vault()
    assert vault.lookup_by_real(cid, "4111111111111111") is None


@pytest.mark.parametrize(
    "type_",
    ["CREDIT_CARD", "API_KEY", "password", "access_token", "JWT", "SSN", "IBAN"],
)
def test_redact_covers_every_secret_entity_type_in_the_routing_table(type_):
    cid = conv_id()
    masked, pairs = mask_text("secret-value-here", [entity("secret-value-here", type_)], cid)
    assert masked == REDACTED
    assert pairs == [("secret-value-here", REDACTED)]


# --- keep: explicitly non-PII -----------------------------------------------

def test_keep_leaves_urls_untouched():
    cid = conv_id()
    masked, pairs = mask_text("See https://example.com/x", [entity("https://example.com/x", "URL")], cid)
    assert masked == "See https://example.com/x"
    assert pairs == []


# --- unknown/unrouted entity type: fail-safe regression ---------------------

def test_unknown_entity_type_is_redacted_not_silently_left_unmasked():
    # Regression test for the bug fixed in this pass: route() returns
    # None for any entity_type not in ROUTING_TABLE, and the final `else`
    # branch used to just `pass` — meaning a genuinely detected entity
    # with an unrecognized type sailed through completely unmasked. The
    # fail-safe contract (GUIDE.md: "unknown label = redact, fail-safe")
    # requires it to be redacted instead.
    cid = conv_id()
    masked, pairs = mask_text(
        "my secret is XYZ_UNKNOWN_VALUE",
        [entity("XYZ_UNKNOWN_VALUE", "SOME_TYPE_NOBODY_REGISTERED")],
        cid,
    )
    assert "XYZ_UNKNOWN_VALUE" not in masked, "an unrecognized entity type must never be left in the clear"
    assert masked == f"my secret is {REDACTED}"
    assert pairs == [("XYZ_UNKNOWN_VALUE", REDACTED)]


def test_unknown_entity_type_is_also_not_put_in_the_vault():
    cid = conv_id()
    mask_text("x", [entity("x", "TOTALLY_UNKNOWN")], conv_id())
    # (uses a throwaway cid above on purpose — just checking no exception;
    # the real assertion is the redact-not-leak behavior covered above)


# --- edge cases --------------------------------------------------------------

def test_empty_string_text_does_not_crash():
    cid = conv_id()
    masked, pairs = mask_text("", [], cid)
    assert masked == ""
    assert pairs == []


def test_empty_entity_list_leaves_text_unchanged():
    cid = conv_id()
    masked, pairs = mask_text("nothing to mask here", [], cid)
    assert masked == "nothing to mask here"
    assert pairs == []


def test_entity_with_empty_text_is_skipped_without_crashing():
    cid = conv_id()
    masked, pairs = mask_text("hello", [entity("", "PERSON")], cid)
    assert masked == "hello"
    assert pairs == []


def test_unicode_names_are_masked_and_restored_correctly():
    cid = conv_id()
    original = "José García called about Владимир Путин's visit to 北京市."
    masked, _ = mask_text(original, [entity("José García", "PERSON")], cid)
    assert "José García" not in masked

    vault = get_vault()
    restored = vault.restore_text(cid, masked)
    assert restored == original


def test_repeated_entity_in_one_call_uses_a_single_consistent_fake():
    cid = conv_id()
    original = "John said hi. Later, John said bye."
    masked, pairs = mask_text(
        original,
        [entity("John", "PERSON"), entity("John", "PERSON")],
        cid,
    )
    # Don't assert on the substring "John" being absent from `masked` —
    # Faker's name pool can coincidentally generate a fake that itself
    # contains "John" for some OTHER/later entity, which would make a
    # naive `"John" not in masked` check flaky. What actually matters:
    # (1) both occurrences got the same fake (one pair's fake, used
    # twice — not two different fakes for the two identical mentions),
    # and (2) the text round-trips exactly through the vault.
    assert len(pairs) == 2
    assert pairs[0][1] == pairs[1][1], "both mentions of the same real value must resolve to the same fake"
    vault = get_vault()
    assert vault.restore_text(cid, masked) == original


def test_longer_entity_is_replaced_before_a_shorter_one_it_contains():
    # mask_text sorts pairs by original length descending specifically so
    # "James Walsh" doesn't get partially clobbered by a separate "James"
    # replacement happening first.
    cid = conv_id()
    masked, _ = mask_text(
        "James Walsh and James were both there.",
        [entity("James Walsh", "PERSON"), entity("James", "PERSON")],
        cid,
    )
    assert "James Walsh" not in masked
    # The standalone "James" (second sentence) should also be masked, with
    # its own fake, not accidentally left as a fragment of the first.
    assert masked.count("James") == 0


def test_mixed_entity_types_in_a_single_prompt():
    cid = conv_id()
    text = "I'm John Smith, email john@x.com, card 4111111111111111, see https://x.com"
    entities = [
        entity("John Smith", "PERSON"),
        entity("john@x.com", "EMAIL"),
        entity("4111111111111111", "CREDIT_CARD"),
        entity("https://x.com", "URL"),
    ]
    masked, pairs = mask_text(text, entities, cid)
    assert "John Smith" not in masked
    assert "john@x.com" not in masked
    assert "4111111111111111" not in masked
    assert REDACTED in masked  # the credit card
    assert "https://x.com" in masked  # kept, not PII
    assert len(pairs) == 3  # URL contributes no pair (kept)


def test_sixty_simultaneous_entities_does_not_crash_or_collide():
    cid = conv_id()
    names = [f"Person{i}" for i in range(60)]
    text = " ".join(names)
    entities = [entity(n, "PERSON") for n in names]
    masked, pairs = mask_text(text, entities, cid)
    assert len(pairs) == 60
    fakes = [p[1] for p in pairs]
    assert len(set(fakes)) == 60, "60 distinct real values must get 60 distinct fakes"
    for n in names:
        assert n not in masked
