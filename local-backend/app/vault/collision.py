"""Collision detection for the mapping vault.

Ensures bijective mapping: no fake value maps to two different reals,
and no real value maps to two different fakes within the same conversation.
"""
from __future__ import annotations

import random
import string
from typing import Optional

from faker import Faker

_faker = Faker()


class CollisionError(Exception):
    """Raised when a fake value would collide with an existing mapping."""
    pass


def generate_fake_name() -> str:
    """Generate a realistic fake person name."""
    return _faker.name()


def generate_fake_email() -> str:
    """Generate a realistic fake email address."""
    return _faker.email()


def generate_fake_phone() -> str:
    """Generate a fake E.164 phone number."""
    return f"+1{_faker.numerify('##########')}"


def generate_fake_org() -> str:
    """Generate a fake organization name."""
    return _faker.company()


def generate_fake_address() -> str:
    """Generate a fake street address."""
    return _faker.address().replace("\n", ", ")


def generate_fake_username() -> str:
    """Generate a fake username."""
    return _faker.user_name()


def generate_unique_fake(
    existing_fakes: set[str],
    generator_func,
    max_attempts: int = 100,
) -> str:
    """Generate a fake value that doesn't collide with existing ones.

    Args:
        existing_fakes: set of fake values already in use in this conversation.
        generator_func: function that generates a candidate (e.g., generate_fake_name).
        max_attempts: how many times to try before giving up.

    Returns:
        A unique fake value.

    Raises:
        CollisionError if no unique value could be generated after max_attempts.
    """
    for _ in range(max_attempts):
        candidate = generator_func()
        if candidate not in existing_fakes:
            return candidate

    # Last resort: append a random suffix
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    candidate = f"{generator_func()}_{suffix}"
    if candidate not in existing_fakes:
        return candidate

    raise CollisionError(
        f"Could not generate a unique fake value after {max_attempts} attempts"
    )
