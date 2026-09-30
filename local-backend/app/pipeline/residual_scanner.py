"""Independent residual scanner — the last gate before send.

Runs deterministic checks on the MASKED output to catch anything the
detection pipeline missed. This is INDEPENDENT of detection.py —
it does not share code with the primary detector.

If this scanner finds any leaks, the request is fail-closed.

Checks:
  - Luhn-valid credit card numbers
  - JWT shape (three base64url segments)
  - API key prefixes (AKIA, sk-, ghp_, xoxb-, etc.)
  - PEM block headers
  - IBAN mod-97
  - High-entropy strings (potential secrets)

Note: this scanner CANNOT catch missed names. There is no checksum
for "this is a person's name." That is a stated structural limit.
"""
from __future__ import annotations

import re
import base64
import math


class ResidualScanner:
    """Independent scanner that runs on masked text."""

    def scan(self, text: str) -> list[dict]:
        """Scan text for residual PII.

        Returns:
            List of findings: [{type, text, start, end, reason}, ...]
            Empty list means clean — safe to send.
        """
        findings = []

        findings.extend(self._find_credit_cards(text))
        findings.extend(self._find_jwts(text))
        findings.extend(self._find_api_keys(text))
        findings.extend(self._find_pem_blocks(text))
        findings.extend(self._find_ibans(text))
        findings.extend(self._find_high_entropy(text))

        # Sort by position
        findings.sort(key=lambda f: f["start"])
        return findings

    def _find_credit_cards(self, text: str) -> list[dict]:
        """Find Luhn-valid credit card numbers."""
        findings = []
        pattern = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

        for m in pattern.finditer(text):
            candidate = m.group()
            digits = re.sub(r"[^\d]", "", candidate)
            if 13 <= len(digits) <= 19 and self._luhn_check(digits):
                findings.append({
                    "type": "CREDIT_CARD",
                    "text": candidate,
                    "start": m.start(),
                    "end": m.end(),
                    "reason": "Luhn-valid card number detected in masked output",
                })
        return findings

    def _find_jwts(self, text: str) -> list[dict]:
        """Find JWT-shaped tokens."""
        findings = []
        pattern = re.compile(r"\b([A-Za-z0-9_-]{10,})\.([A-Za-z0-9_-]{10,})\.([A-Za-z0-9_-]{10,})\b")

        for m in pattern.finditer(text):
            try:
                for seg in m.groups():
                    padded = seg + "=" * (4 - len(seg) % 4)
                    base64.urlsafe_b64decode(padded)
                findings.append({
                    "type": "JWT",
                    "text": m.group(),
                    "start": m.start(),
                    "end": m.end(),
                    "reason": "Valid JWT shape detected in masked output",
                })
            except (ValueError, base64.binascii.Error):
                continue
        return findings

    def _find_api_keys(self, text: str) -> list[dict]:
        """Find API keys by provider prefix."""
        findings = []
        patterns = [
            (r"\bAKIA[0-9A-Z]{16}\b", "AWS_ACCESS_KEY"),
            (r"\bsk-[A-Za-z0-9]{40,}\b", "OPENAI_API_KEY"),
            (r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b", "GITHUB_PAT"),
            (r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b", "SLACK_TOKEN"),
            (r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b", "STRIPE_KEY"),
            (r"\bAIza[0-9A-Za-z_-]{35}\b", "GOOGLE_API_KEY"),
        ]

        for pattern_str, key_type in patterns:
            for m in re.finditer(pattern_str, text):
                findings.append({
                    "type": key_type,
                    "text": m.group(),
                    "start": m.start(),
                    "end": m.end(),
                    "reason": f"{key_type} prefix detected in masked output",
                })
        return findings

    def _find_pem_blocks(self, text: str) -> list[dict]:
        """Find PEM blocks (private keys, certificates)."""
        findings = []
        pattern = re.compile(
            r"-----BEGIN (?:[A-Z ]+)-----[\s\S]+?-----END (?:[A-Z ]+)-----"
        )
        for m in pattern.finditer(text):
            findings.append({
                "type": "PEM_BLOCK",
                "text": m.group()[:50] + "...",
                "start": m.start(),
                "end": m.end(),
                "reason": "PEM block detected in masked output",
            })
        return findings

    def _find_ibans(self, text: str) -> list[dict]:
        """Find IBANs that pass mod-97."""
        findings = []
        pattern = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b")

        for m in pattern.finditer(text):
            candidate = m.group()
            if self._iban_check(candidate):
                findings.append({
                    "type": "IBAN",
                    "text": candidate,
                    "start": m.start(),
                    "end": m.end(),
                    "reason": "mod-97 valid IBAN detected in masked output",
                })
        return findings

    def _find_high_entropy(self, text: str) -> list[dict]:
        """Find high-entropy strings that might be secrets.

        Looks for strings of 20+ chars with high character diversity
        (potential passwords, tokens, or keys that weren't caught by
        prefix matching).
        """
        findings = []
        # Match long alphanumeric strings (no spaces)
        pattern = re.compile(r"\b[A-Za-z0-9+/=_-]{20,}\b")

        for m in pattern.finditer(text):
            candidate = m.group()
            entropy = self._shannon_entropy(candidate)

            # Threshold: 3.5 bits/char is high entropy
            if entropy > 3.5:
                findings.append({
                    "type": "HIGH_ENTROPY",
                    "text": candidate[:30] + "..." if len(candidate) > 30 else candidate,
                    "start": m.start(),
                    "end": m.end(),
                    "reason": f"High entropy string ({entropy:.1f} bits/char) — possible secret",
                })
        return findings

    @staticmethod
    def _luhn_check(digits: str) -> bool:
        """Luhn checksum validation."""
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

    @staticmethod
    def _iban_check(iban: str) -> bool:
        """IBAN mod-97 validation."""
        cleaned = iban.replace(" ", "").upper()
        if len(cleaned) < 15 or len(cleaned) > 34:
            return False
        if not cleaned[:2].isalpha() or not cleaned[2:4].isdigit():
            return False
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

    @staticmethod
    def _shannon_entropy(s: str) -> float:
        """Calculate Shannon entropy of a string (bits per character)."""
        if not s:
            return 0.0
        freq = {}
        for ch in s:
            freq[ch] = freq.get(ch, 0) + 1
        length = len(s)
        entropy = 0.0
        for count in freq.values():
            p = count / length
            entropy -= p * math.log2(p)
        return entropy


# Module-level convenience function
def scan(text: str) -> list[dict]:
    """Scan text for residual PII. Returns list of findings (empty = clean)."""
    scanner = ResidualScanner()
    return scanner.scan(text)
