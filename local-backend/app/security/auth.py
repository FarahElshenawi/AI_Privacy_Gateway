"""Per-install shared-secret token handshake. Owner: Role 2.

Generated per-install on first launch by the extension (background/service-worker.js),
stored in chrome.storage.local, shared via a one-time handshake. Never hardcoded
in the extension bundle. See docs/architecture.md 1.4.
"""

def verify_token(token: str) -> bool:
    # TODO: constant-time compare against stored per-install token
    raise NotImplementedError
