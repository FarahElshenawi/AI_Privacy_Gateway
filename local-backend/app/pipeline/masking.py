"""Masking — generates (original, replacement) pairs for detected entities.

Uses the routing table to decide the masking strategy per entity type:
  - faker:   identity PII (names, emails, phones, orgs) → fake surrogates
  - redact:  secrets (cards, keys, JWTs, passwords) → [[REDACTED]]
  - keep:    non-PII (URLs, IPs in some contexts) → unchanged

The masker outputs (original, replacement) pairs that reconstructors apply
to files. The vault stores the reverse mapping for response restoration.
"""
from __future__ import annotations

from typing import Optional

from app.pipeline.routing import route
from app.vault.store import get_vault
from app.vault.collision import (
    generate_unique_fake,
    generate_fake_name,
    generate_fake_email,
    generate_fake_phone,
    generate_fake_org,
    generate_fake_address,
    generate_fake_username,
)

# Redaction placeholder for secrets
REDACTED = "[[REDACTED]]"

# Faker generators per entity type
_FAKER_GENERATORS = {
    "PERSON": generate_fake_name,
    "person": generate_fake_name,
    "EMAIL": generate_fake_email,
    "email": generate_fake_email,
    "PHONE_E164": generate_fake_phone,
    "PHONE_NUMBER": generate_fake_phone,
    "phone_number": generate_fake_phone,
    "ORGANIZATION": generate_fake_org,
    "organization": generate_fake_org,
    "ADDRESS": generate_fake_address,
    "address": generate_fake_address,
    "USERNAME": generate_fake_username,
    "username": generate_fake_username,
}


def mask_entities(entities: list[dict], conversation_id: str = "default") -> list[tuple[str, str]]:
    """Generate (original, replacement) pairs for detected entities.

    Args:
        entities: list of entity dicts with 'type' and 'text' keys.
        conversation_id: the conversation this masking belongs to.

    Returns:
        List of (original_text, replacement_text) tuples.
        These pairs are passed to reconstructors and also stored in the vault.
    """
    vault = get_vault()
    pairs = []
    existing_fakes = set(vault.get_conversation(conversation_id).get_all_fakes()) if vault.get_conversation(conversation_id) else set()

    for entity in entities:
        entity_type = entity.get("type", "")
        original_text = entity.get("text", "")

        if not original_text:
            continue

        # Get the routing decision
        action = route(entity_type)

        if action == "redact":
            # Secrets: replace with [[REDACTED]]
            pairs.append((original_text, REDACTED))

        elif action == "faker":
            # Identity PII: generate a fake surrogate.
            #
            # Reuse an existing mapping for this exact real value within
            # the conversation if one exists, rather than always minting a
            # new fake. Two things depend on this:
            #   1. The repo's own Week-1 test checklist (GUIDE.md): "Mask
            #      mode: ... second message in same chat reuses the same
            #      fake for 'John'" — a previous version of this function
            #      only checked the new candidate against existing_fakes
            #      (collision avoidance), never against "has this real
            #      value already been mapped," so the same name got a
            #      DIFFERENT fake on every call, breaking that contract.
            #   2. The same entity appearing twice in ONE call no longer
            #      wastes a second vault entry for the same real value.
            existing_entry = vault.lookup_by_real(conversation_id, original_text)
            if existing_entry is not None:
                fake_value = existing_entry.fake_value
            else:
                generator = _FAKER_GENERATORS.get(entity_type)
                if generator is None:
                    # Unknown type with faker routing — use a generic name
                    generator = generate_fake_name

                fake_value = generate_unique_fake(existing_fakes, generator)
                existing_fakes.add(fake_value)

                # Store in vault for later restoration
                vault.add_mapping(
                    conversation_id=conversation_id,
                    fake_value=fake_value,
                    real_value=original_text,
                    entity_type=entity_type,
                )

            pairs.append((original_text, fake_value))

        elif action == "keep":
            # Non-PII or explicitly kept — no masking
            pass

        else:
            # Unknown/unrouted entity type (route() returned None — not
            # present in ROUTING_TABLE). This is a genuinely detected
            # entity with nowhere documented to send it, so the fail-safe
            # choice is to redact it, matching the documented contract
            # (docs/development-plan.md Week 1 GUIDE: "unknown label =
            # redact, fail-safe") and apply_masking()'s reference
            # implementation (`policy.get(s.label, "redact")`).
            #
            # A previous version of this branch did `pass` here — meaning
            # an entity with an unrecognized type was silently left
            # UNMASKED in the output. That inverted the fail-safe: a typo
            # in an entity-type string, a new label Role 1's detector
            # starts emitting, or a casing mismatch would all cause real
            # PII to sail through completely unredacted with no error,
            # log line, or test failure anywhere. Redacting-by-default
            # means an unrecognized type degrades to "the LLM doesn't see
            # this value" instead of "the LLM sees the raw PII."
            pairs.append((original_text, REDACTED))

    return pairs


def apply_pairs(text: str, pairs: list[tuple[str, str]]) -> tuple[str, int]:
    """Apply (original, replacement) pairs to a block of text in one safe pass.

    Sorts by original length descending before substituting, so a longer
    match (e.g. "a@x.com.au") is replaced before a shorter match it
    CONTAINS (e.g. "a@x.com") would otherwise partially clobber it,
    leaving a mangled fragment like "B.au" instead of either value
    cleanly replaced.

    This is the one place that logic lives. It used to be duplicated
    (inconsistently — without the sort) inside mask_text() below AND
    inside each multimodal reconstructor's own ad hoc replacement loop
    (text_reconstructor.py, excel_reconstructor.py, word_reconstructor.py
    each did `for original, replacement in pairs: text = text.replace(...)`
    with no sort at all), so any file containing one PII value that is a
    substring of another carried the same corruption risk mask_text()
    guards against for conversation text. All of those now call this
    function instead.

    Returns (new_text, count) where count is how many of the given pairs
    actually matched something in the text (not just how many were
    given — a pair whose original text isn't present contributes 0).
    """
    sorted_pairs = sorted(pairs, key=lambda p: len(p[0]), reverse=True)
    count = 0
    for original, replacement in sorted_pairs:
        if original and original in text:
            text = text.replace(original, replacement)
            count += 1
    return text, count


def mask_text(text: str, entities: list[dict], conversation_id: str = "default") -> tuple[str, list[tuple[str, str]]]:
    """Mask text and return the masked string + pairs.

    Convenience function that calls mask_entities and applies the pairs
    to the text string.

    Args:
        text: the input text to mask.
        entities: list of entity dicts with 'type' and 'text' keys.
        conversation_id: the conversation this belongs to.

    Returns:
        (masked_text, pairs) where pairs is the list of (original, replacement).
    """
    pairs = mask_entities(entities, conversation_id)
    masked_text, _ = apply_pairs(text, pairs)
    return masked_text, pairs
