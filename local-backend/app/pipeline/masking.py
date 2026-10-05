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
            # Identity PII: generate a fake surrogate
            # Check if this real value already has a mapping in this conversation
            existing_entry = vault.lookup_by_real(conversation_id, original_text)
            if existing_entry is not None:
                # Reuse existing mapping — same person gets same fake across calls
                pairs.append((original_text, existing_entry.fake_value))
                continue

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
            # Unknown routing — fail-safe: redact rather than leak
            pairs.append((original_text, REDACTED))

    return pairs


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

    masked_text = text
    # Sort by original length descending so longer matches are replaced first
    sorted_pairs = sorted(pairs, key=lambda p: len(p[0]), reverse=True)
    for original, replacement in sorted_pairs:
        masked_text = masked_text.replace(original, replacement)

    return masked_text, pairs


def apply_pairs(text: str, pairs: list[tuple[str, str]]) -> tuple[str, int]:
    """Apply (original, replacement) pairs to text, longest originals first.

    Returns (masked_text, replacement_count).
    Used by the multimodal pipeline and file reconstruction layer.
    """
    masked = text
    count = 0
    for original, replacement in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
        if original and original in masked:
            count += masked.count(original)
            masked = masked.replace(original, replacement)
    return masked, count
