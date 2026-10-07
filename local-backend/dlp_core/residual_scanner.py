"""Independent residual scanner — shares no patterns with Tier 1.

Scans MASKED output for surviving structured PII. Only checks entities with
hard checksums (cards, IBANs) or unique prefixes (API keys, JWTs) — NOT
emails/phones, because Faker surrogates also match those patterns.

If any finding is returned, the request must fail-closed.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

_CC = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.ey[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{0,}\b")
_API_KEYS = [
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"),
]
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _plausible_issuer(d: str) -> bool:
    """Independent (deliberately simple) issuer check, so random Luhn-valid numbers such as
    epoch-millisecond timestamps or order ids don't fail-close a request (~10% of them pass Luhn)."""
    n = len(d)
    p2, p3, p4 = int(d[:2]), int(d[:3]), int(d[:4])
    return ((d[0] == "4" and n in (13, 16, 19)) or (n == 16 and (51 <= p2 <= 55 or 2221 <= p4 <= 2720))
            or (n == 15 and p2 in (34, 37)) or (16 <= n <= 19 and (p4 == 6011 or p2 in (62, 65) or 644 <= p3 <= 649))
            or (14 <= n <= 19 and (300 <= p3 <= 305 or p2 in (36, 38, 39))) or (16 <= n <= 19 and 3528 <= p4 <= 3589))


def scan(text: str) -> list[dict]:
    """Scan masked output for surviving PII with hard evidence.

    Returns list of findings: [{type, start, end, reason}, ...] (never the matched value)
    Empty list = safe to send.
    """
    findings = []

    for m in _CC.finditer(text):
        digits = re.sub(r"[^\d]", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits) and _plausible_issuer(digits):
            findings.append({"type": "CREDIT_CARD", "start": m.start(), "end": m.end(), "reason": "Luhn-valid card survived"})

    for m in _JWT.finditer(text):
        parts = m.group().split(".")
        if len(parts) == 3:
            try:
                header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
                if isinstance(header, dict) and "alg" in header:
                    findings.append({"type": "AUTH_TOKEN", "start": m.start(), "end": m.end(), "reason": "Valid JWT survived"})
            except (binascii.Error, ValueError, UnicodeDecodeError):
                pass

    for pat in _API_KEYS:
        for m in pat.finditer(text):
            findings.append({"type": "API_KEY", "start": m.start(), "end": m.end(), "reason": "API key prefix survived"})

    for m in _IBAN.finditer(text):
        s = m.group().replace(" ", "").replace("-", "")
        try:
            rearranged = s[4:] + s[:4]
            numeric = "".join(str(int(c, 36)) for c in rearranged)
            if int(numeric) % 97 == 1:
                findings.append({"type": "IBAN", "start": m.start(), "end": m.end(), "reason": "Valid IBAN survived"})
        except (ValueError, OverflowError):
            pass

    if "-----BEGIN" in text and "PRIVATE KEY" in text:
        findings.append({"type": "PRIVATE_KEY", "start": text.find("-----BEGIN"), "end": len(text), "reason": "PEM block survived"})

    return findings
