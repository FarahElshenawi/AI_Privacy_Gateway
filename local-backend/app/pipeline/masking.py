"""Step 4 — Masking. Owner: Role 3 (mirrors extension/src/lib/masking.js —
kept in sync manually until a shared schema package exists).

faker()   -> synthetic substitution
redact()  -> hard block
context() -> anchor + description (fallback path only, see routing table caveat
             in docs/architecture.md 1.2: reversibility depends on the LLM
             echoing the anchor verbatim, which is not guaranteed)
"""

def faker(entity: dict) -> str:
    raise NotImplementedError

def redact(entity: dict) -> str:
    raise NotImplementedError

def context(entity: dict) -> str:
    raise NotImplementedError
