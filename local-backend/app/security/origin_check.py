"""Origin check middleware. Owner: Role 2. Combined with auth.py's token check
on every request to the Local Backend (docs/architecture.md 1.4)."""

ALLOWED_ORIGINS = {"chrome-extension://<extension-id-here>"}

def is_allowed_origin(origin: str) -> bool:
    return origin in ALLOWED_ORIGINS
