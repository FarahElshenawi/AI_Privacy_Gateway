"""Origin check — only the local machine's browser EXTENSION may call this backend.

Three independent checks, all of which must pass:

  1. The TCP peer is loopback (no remote access).
  2. If the request carries an `Origin` header it must be OUR extension's origin (pinned ID).
     A web page (chatgpt.com, or any site an attacker controls) ALWAYS sends its own
     origin on cross-origin requests and cannot forge it, so this stops page script from
     reading /token or calling /api/*. Non-browser clients (curl, tests) send no Origin.
  3. The `Host` header must be a loopback name. This stops DNS rebinding, where an attacker
     domain is re-pointed at 127.0.0.1 so the page becomes "same-origin" with the backend
     (and sends no Origin header at all) while still sending `Host: attacker.example`.
"""
from __future__ import annotations

import os
import re

from fastapi import Request, HTTPException


ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}          # TCP peer addresses
ALLOWED_HOST_HEADERS = {"localhost", "127.0.0.1", "[::1]"}  # `Host` header names (any port)
EXTENSION_ORIGIN_PREFIX = "chrome-extension://"

# The extension's ID is pinned by the `key` in extension/manifest.json, so an unpacked build and
# a force-installed build share it. Only these IDs may call the backend from a browser. Add the
# Chrome Web Store ID (or others) with DLP_EXTENSION_IDS="id1,id2".
PINNED_EXTENSION_ID = "efcejekkbkbbknfpjgbpkojgnoomggbi"
_ID_RE = re.compile(r"^[a-p]{32}$")


def allowed_extension_origins() -> set[str]:
    ids = {PINNED_EXTENSION_ID}
    for raw in os.environ.get("DLP_EXTENSION_IDS", "").split(","):
        raw = raw.strip().lower()
        if _ID_RE.match(raw):
            ids.add(raw)
    return {EXTENSION_ORIGIN_PREFIX + i for i in ids}


def _host_name(host_header: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1'; '[::1]:8765' -> '[::1]'."""
    h = host_header.strip().lower()
    if h.startswith("["):
        return h.split("]", 1)[0] + "]"
    return h.rsplit(":", 1)[0] if ":" in h else h


def check_origin(request: Request) -> None:
    """Raise HTTPException(403) unless the request is from the local extension/client."""
    client_host = request.client.host if request.client else ""
    if client_host not in ALLOWED_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: requests from {client_host} are not allowed. "
                   "The backend only accepts localhost connections.",
        )

    origin = request.headers.get("origin")
    if origin is not None and origin.lower() not in allowed_extension_origins():
        raise HTTPException(status_code=403, detail="Access denied: only the Doppel extension may call this backend.")

    host = request.headers.get("host")
    if host is not None and _host_name(host) not in ALLOWED_HOST_HEADERS:
        raise HTTPException(status_code=403, detail="Access denied: unexpected Host header.")
