"""Step 5 — Independent Residual Scanner. Owner: Role 1.

Does NOT depend on detection.py's output — this is what makes it catch
detection misses, not just masking-stage bugs (docs/architecture.md 1.2).
Luhn check, JWT shape, PEM headers, key-prefix matching, entropy checks.

Fail-closed: if this scanner finds a leak, block the request. Do not log
the matched value anywhere outside CP1 (local, 24h expiry).
"""

def scan(masked_text: str) -> list[dict]:
    """Returns any residual PII-shaped matches found in already-masked text."""
    findings = []
    # TODO: Luhn check for card-like number sequences
    # TODO: JWT shape (three base64url segments separated by '.')
    # TODO: PEM headers ('-----BEGIN ... KEY-----')
    # TODO: key-prefix matching (sk-, ghp_, AKIA, etc.)
    # TODO: entropy check on remaining tokens
    return findings
