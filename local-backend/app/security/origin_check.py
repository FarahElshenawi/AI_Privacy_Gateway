"""Origin check — ensures requests come from allowed sources.

The local backend only accepts requests from:
  1. localhost / 127.0.0.1 (the browser extension)
  2. The same machine (no remote access)
"""
from __future__ import annotations

from fastapi import Request, HTTPException


ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}


def check_origin(request: Request) -> None:
    """Verify the request originates from localhost.

    Raises HTTPException if the request comes from a non-local source.
    """
    client_host = request.client.host if request.client else ""
    if client_host not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: requests from {client_host} are not allowed. "
                   "The backend only accepts localhost connections.",
        )
