"""Backend authentication — per-install token.

Generates a random token on first run. The browser extension must
include this token in the Authorization header. This prevents other
websites from calling the local backend (since they don't have the token).

The token file is private to the current user (mode 0600): the token is the only thing
standing between another local user/process and the vault-backed /api/demask endpoint.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional

_TOKEN_FILE = Path.home() / ".pii_gateway_token.json"
_MIN_TOKEN_LEN = 16


def _restrict(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:      # e.g. unsupported filesystem / Windows ACLs: best effort
        pass


def _write_token(token: str) -> None:
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start, so the token is never briefly world-readable.
    fd = os.open(_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"token": token}, f)
    _restrict(_TOKEN_FILE)


def get_install_token() -> str:
    """Get or create the per-install token.

    The token is generated once and stored in the user's home directory.
    The extension reads this token during setup and includes it in every
    request to the backend. A missing, corrupt, empty or too-short token is replaced.
    """
    if _TOKEN_FILE.exists():
        try:
            with open(_TOKEN_FILE, "r") as f:
                token = json.load(f).get("token")
            if isinstance(token, str) and len(token) >= _MIN_TOKEN_LEN:
                _restrict(_TOKEN_FILE)      # tighten files written by older versions
                return token
        except (json.JSONDecodeError, AttributeError, OSError):
            pass

    token = secrets.token_urlsafe(32)
    _write_token(token)
    return token


def verify_token(token: Optional[str]) -> bool:
    """Verify that the provided token matches the install token."""
    if not token:                 # None or "" ("Authorization: Bearer " must never match)
        return False
    expected = get_install_token()
    return secrets.compare_digest(token.encode(), expected.encode())
