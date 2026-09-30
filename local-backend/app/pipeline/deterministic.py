"""Step 1a — Deterministic detection tier. Owner: Detection Engine.

Finds structured PII in free text using pattern matching + checksum validation.
This is the tier that catches ~50% of the entity space with ~100% precision
and single-digit-millisecond latency. See docs/architecture.md §1 (Detection
Engine) and the Tiered Detection Matrix figure.

Design rules:
    1. Checksums (Luhn, mod-97) over regex shape — never trust shape alone.
    2. Conservative false-positive bias: better to miss than to false-alarm.
    3. Each finder returns character spans in the same shape as the semantic
       tier, so the orchestrator can merge results without caring which tier
       produced them.
    4. No shared state, no I/O, no model loading — pure functions.

Interface (matches detection.py):
    detect(text) -> list[{
        "type": str,         # entity type, see TYPE_* constants
        "text": str,         # matched substring
        "start": int,        # 0-indexed char offset
        "end": int,          # exclusive end offset
        "confidence": float, # always 1.0 for deterministic matches
        "source": "deterministic",
    }]
"""
from __future__ import annotations

import re
import base64
from typing import TypedDict


# === Entity type constants ===
TYPE_CREDIT_CARD = "CREDIT_CARD"
TYPE_API_KEY = "API_KEY"
TYPE_JWT = "JWT"
TYPE_PEM_BLOCK = "PEM_BLOCK"
TYPE_IBAN = "IBAN"
TYPE_IPV4 = "IPV4"
TYPE_IPV6 = "IPV6"
TYPE_EMAIL = "EMAIL"
TYPE_PHONE_E164 = "PHONE_E164"


class Entity(TypedDict):
    type: str
    text: str
    start: int
    end: int
    confidence: float
    source: str


# === Luhn algorithm (credit cards, IMEI, some national ID numbers) ===

def _luhn_checksum(digits: str) -> bool:
    """Return True if the digit string passes the Luhn check."""
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        if not ch.isdigit():
            return False
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# === Mod-97 (IBAN) ===

def _iban_checksum(iban: str) -> bool:
    """Return True if the IBAN passes mod-97 validation."""
    cleaned = iban.replace(" ", "").upper()
    if len(cleaned) < 15 or len(cleaned) > 34:
        return False
    if not cleaned[:2].isalpha() or not cleaned[2:4].isdigit():
        return False
    # Move first 4 chars to end, replace letters with digits (A=10, B=11, ...)
    rearranged = cleaned[4:] + cleaned[:4]
    numeric = ""
    for ch in rearranged:
        if ch.isdigit():
            numeric += ch
        elif ch.isalpha():
            numeric += str(ord(ch) - 55)
        else:
            return False
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


# === Pattern finders ===

# Credit card: 13-19 digits, optional spaces or dashes between groups
_CC_PATTERN = re.compile(
    r"\b(?:\d[ -]*?){13,19}\b"
)

def find_credit_cards(text: str) -> list[Entity]:
    """Find credit-card-like number sequences validated by Luhn."""
    results = []
    for m in _CC_PATTERN.finditer(text):
        candidate = m.group()
        digits = re.sub(r"[^\d]", "", candidate)
        if 13 <= len(digits) <= 19 and _luhn_checksum(digits):
            results.append(Entity(
                type=TYPE_CREDIT_CARD,
                text=candidate,
                start=m.start(),
                end=m.end(),
                confidence=1.0,
                source="deterministic",
            ))
    return results


# API keys — prefix-based, by provider
# Format: (provider, regex, expected_total_length_or_None)
_API_KEY_PATTERNS = [
    # AWS access key ID: AKIA + 16 base64 chars
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 20),
    # AWS secret key (40 base64 chars, no clear prefix — only match if labeled)
    # (skip standalone AWS secret — too many false positives)
    # OpenAI: sk- followed by 48+ base64-url chars
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"), None),
    # GitHub personal access tokens (classic + fine-grained)
    ("github_pat", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"), None),
    # Slack tokens
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), None),
    # Stripe live keys
    ("stripe_key", re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b"), None),
    # Google API key (AIza + 35 base64 chars)
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), None),
    # Generic: matches the shape but doesn't claim a provider
    # (commented out — too many false positives; rely on the specific patterns)
]

def find_api_keys(text: str) -> list[Entity]:
    """Find provider-specific API keys by prefix + length."""
    results = []
    seen_spans = set()
    for provider, pattern, _expected_len in _API_KEY_PATTERNS:
        for m in pattern.finditer(text):
            if (m.start(), m.end()) in seen_spans:
                continue
            seen_spans.add((m.start(), m.end()))
            results.append(Entity(
                type=TYPE_API_KEY,
                text=m.group(),
                start=m.start(),
                end=m.end(),
                confidence=1.0,
                source="deterministic",
            ))
    return results


# JWT: three base64url segments separated by dots
_JWT_PATTERN = re.compile(
    r"\b([A-Za-z0-9_-]{10,})\.([A-Za-z0-9_-]{10,})\.([A-Za-z0-9_-]{10,})\b"
)

def find_jwts(text: str) -> list[Entity]:
    """Find JWT-shaped tokens (header.payload.signature)."""
    results = []
    for m in _JWT_PATTERN.finditer(text):
        # Try to decode each segment as base64url — validates shape
        try:
            for seg in m.groups():
                # JWT segments are base64url without padding
                padded = seg + "=" * (4 - len(seg) % 4)
                base64.urlsafe_b64decode(padded)
            results.append(Entity(
                type=TYPE_JWT,
                text=m.group(),
                start=m.start(),
                end=m.end(),
                confidence=1.0,
                source="deterministic",
            ))
        except (ValueError, base64.binascii.Error):
            continue
    return results


# PEM blocks (private keys, certificates)
_PEM_PATTERN = re.compile(
    r"-----BEGIN (?:[A-Z ]+)-----"
    r"[\s\S]+?"
    r"-----END (?:[A-Z ]+)-----"
)

def find_pem_blocks(text: str) -> list[Entity]:
    """Find PEM blocks (private keys, certificates)."""
    results = []
    for m in _PEM_PATTERN.finditer(text):
        results.append(Entity(
            type=TYPE_PEM_BLOCK,
            text=m.group(),
            start=m.start(),
            end=m.end(),
            confidence=1.0,
            source="deterministic",
        ))
    return results


# IBAN
_IBAN_PATTERN = re.compile(
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b"
)

def find_ibans(text: str) -> list[Entity]:
    """Find IBANs validated by mod-97."""
    results = []
    for m in _IBAN_PATTERN.finditer(text):
        candidate = m.group()
        if _iban_checksum(candidate):
            results.append(Entity(
                type=TYPE_IBAN,
                text=candidate,
                start=m.start(),
                end=m.end(),
                confidence=1.0,
                source="deterministic",
            ))
    return results


# IPv4
_IPV4_PATTERN = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

def find_ipv4s(text: str) -> list[Entity]:
    """Find IPv4 addresses (octet range validated)."""
    return [
        Entity(type=TYPE_IPV4, text=m.group(), start=m.start(), end=m.end(),
               confidence=1.0, source="deterministic")
        for m in _IPV4_PATTERN.finditer(text)
    ]


# IPv6 (simplified — full form, no IPv4-mapped)
_IPV6_PATTERN = re.compile(
    r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b"
)

def find_ipv6s(text: str) -> list[Entity]:
    """Find full-form IPv6 addresses (no abbreviated forms yet)."""
    return [
        Entity(type=TYPE_IPV6, text=m.group(), start=m.start(), end=m.end(),
               confidence=1.0, source="deterministic")
        for m in _IPV6_PATTERN.finditer(text)
    ]


# Email — RFC 5322 simplified
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

def find_emails(text: str) -> list[Entity]:
    """Find email addresses (simplified RFC 5322)."""
    return [
        Entity(type=TYPE_EMAIL, text=m.group(), start=m.start(), end=m.end(),
               confidence=1.0, source="deterministic")
        for m in _EMAIL_PATTERN.finditer(text)
    ]


# Phone (E.164 only — very conservative)
# Format: +<country_code><number>, total 8-15 digits
_PHONE_E164_PATTERN = re.compile(r"\+\d{8,15}\b")

def find_phones_e164(text: str) -> list[Entity]:
    """Find E.164-format phone numbers (+CC...)."""
    return [
        Entity(type=TYPE_PHONE_E164, text=m.group(), start=m.start(), end=m.end(),
               confidence=1.0, source="deterministic")
        for m in _PHONE_E164_PATTERN.finditer(text)
    ]


# === Public API ===

_FINDERS = [
    find_credit_cards,
    find_api_keys,
    find_jwts,
    find_pem_blocks,
    find_ibans,
    find_ipv4s,
    find_ipv6s,
    find_emails,
    find_phones_e164,
]


def detect(text: str) -> list[Entity]:
    """Run all deterministic finders and merge results.

    Returns entities sorted by start offset, with overlaps resolved by
    preferring the longer match (so an API key starting with 'AKIA' is
    kept over a coincidental credit-card-shaped substring inside it).
    """
    all_findings: list[Entity] = []
    for finder in _FINDERS:
        all_findings.extend(finder(text))

    # Deduplicate — same span + type, keep first occurrence
    seen = set()
    deduped = []
    for e in all_findings:
        key = (e["type"], e["start"], e["end"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    # Sort by start offset, then by length (longer first) for overlap resolution
    deduped.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))

    # Resolve overlaps: prefer longer match
    final: list[Entity] = []
    last_end = -1
    for e in deduped:
        if e["start"] >= last_end:
            final.append(e)
            last_end = e["end"]
        # else: this entity overlaps with a previous (longer) one — drop it
    return final


# === CLI for smoke testing ===

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        # Read from file
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            sample = f.read()
    else:
        sample = """Hey team, my card is 4532 1234 5678 9123 and my email is john.doe@example.com.
        Server IP: 192.168.1.1. AWS key: AKIAIOSFODNN7EXAMPLE. OpenAI key: sk-abc123def456.
        JWT: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature123.
        IBAN: GB82WEST12345698765432. Call me at +14155551234.
        -----BEGIN RSA PRIVATE KEY-----
        MIIEpAIBAAKCAQEA...
        -----END RSA PRIVATE KEY-----"""

    results = detect(sample)
    for r in results:
        print(f"  [{r['type']:<12}] {r['text']!r}")
    print(f"\n{len(results)} entities found.")
