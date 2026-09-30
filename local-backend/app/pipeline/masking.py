"""Masking — generates (original, replacement) pairs for detected entities.

Uses the routing table to decide the masking strategy per entity type:
  - faker:   identity PII (names, emails, phones, orgs) → fake surrogates
  - redact:  secrets (cards, keys, JWTs, passwords) → [[REDACTED]]
  - keep:    non-PII (URLs, IPs in some contexts) → unchanged

The masker outputs (original, replacement) pairs that reconstructors apply
to files. The vault stores the reverse mapping for response restoration.
"""
from __future__ import annotations

import re
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
    pairs: list[tuple[str, str]] = []
    seen_originals: set[str] = set()
    conv = vault.get_conversation(conversation_id)
    existing_fakes = set(conv.get_all_fakes()) if conv else set()

    for entity in entities:
        entity_type = entity.get("type", "")
        original_text = entity.get("text", "")

        if not original_text or original_text in seen_originals:
            continue

        action = route(entity_type)

        if action == "redact":
            seen_originals.add(original_text)
            pairs.append((original_text, REDACTED))

        elif action == "faker":
            seen_originals.add(original_text)

            # Same real value in the same conversation -> same fake, so the
            # LLM sees a consistent identity across messages and files.
            existing = vault.lookup_by_real(conversation_id, original_text)
            if existing is not None:
                pairs.append((original_text, existing.fake_value))
                continue

            generator = _FAKER_GENERATORS.get(entity_type, generate_fake_name)
            fake_value = generate_unique_fake(existing_fakes, generator)
            existing_fakes.add(fake_value)

            vault.add_mapping(
                conversation_id=conversation_id,
                fake_value=fake_value,
                real_value=original_text,
                entity_type=entity_type,
            )
            pairs.append((original_text, fake_value))

        # "keep" / unrouted: leave unchanged

    return pairs


def apply_pairs(text: str, pairs: list[tuple[str, str]]) -> tuple[str, int]:
    """Apply (original, replacement) pairs to text in a single pass.

    Single pass (one regex alternation, longest original first) so that a
    replacement can never be re-matched by a later pair, and a short
    original never clobbers part of a longer one.

    Returns: (new_text, number_of_replacements_made)
    """
    if not text or not pairs:
        return text, 0
    mapping = {orig: repl for orig, repl in pairs if orig}
    if not mapping:
        return text, 0
    pattern = re.compile(
        "|".join(re.escape(o) for o in sorted(mapping, key=len, reverse=True))
    )
    return pattern.subn(lambda m: mapping[m.group(0)], text)


def mask_text(text: str, entities: list[dict], conversation_id: str = "default") -> tuple[str, list[tuple[str, str]]]:
    """Mask text and return the masked string + pairs."""
    pairs = mask_entities(entities, conversation_id)
    masked_text, _ = apply_pairs(text, pairs)
    return masked_text, pairs
