"""Backend authentication — per-install token.

Generates a random token on first run. The browser extension must
include this token in the Authorization header. This prevents other
websites from calling the local backend (since they don't have the token).
"""
from __future__ import annotations

import secrets
import json
from pathlib import Path
from typing import Optional

_TOKEN_FILE = Path.home() / ".pii_gateway_token.json"


def get_install_token() -> str:
    """Get or create the per-install token.

    The token is generated once and stored in the user's home directory.
    The extension reads this token during setup and includes it in every
    request to the backend.
    """
    if _TOKEN_FILE.exists():
        try:
            with open(_TOKEN_FILE, "r") as f:
                data = json.load(f)
                return data.get("token", "")
        except (json.JSONDecodeError, KeyError):
            pass

    # Generate a new token
    token = secrets.token_urlsafe(32)
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_TOKEN_FILE, "w") as f:
        json.dump({"token": token}, f)
    return token


def verify_token(token: Optional[str]) -> bool:
    """Verify that the provided token matches the install token."""
    if token is None:
        return False
    expected = get_install_token()
    return secrets.compare_digest(token, expected)
