"""Recognizers: glue between patterns.py (candidates) and validators.py (verdicts).

Two kinds:
  * PatternRecognizer  - declarative: one regex + optional validator + optional context rule.
  * Custom recognizers - when a candidate needs more than one regex match to judge
    (card number windows, IBAN length trimming, code lists, key/secret proximity).

Every recognizer yields Span(start, end, label, score, "regex", validated) where
`validated=True` means hard evidence (checksum / structural proof), the field the merge
engine ranks highest. Plausibility checks never set it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Union

from . import patterns as P
from . import validators as V
from .types import Span

SOURCE = "regex"
_TRAIL = ".,;:!?)]}'\"`"


def trim_trailing(value: str) -> str:
    return value.rstrip(_TRAIL)


@dataclass(frozen=True)
class PatternRecognizer:
    name: str
    labels: tuple
    pattern: re.Pattern
    group: Union[int, str] = 0
    label_fn: Optional[Callable[[re.Match], str]] = None
    validator: Optional[Callable[[str], bool]] = None
    hard_evidence: bool = False          # validator pass == checksum-grade proof
    score: float = 0.9
    context: Optional[re.Pattern] = None         # searched in the text BEFORE the value
    context_after: Optional[re.Pattern] = None   # searched in the text AFTER the value
    context_required: bool = False
    context_boost: float = 0.0
    demote_on_fail: bool = False         # validator failed but context present: keep at low score
    trim_url: bool = False
    before: int = 40
    after: int = 24

    def scan(self, text: str) -> Iterable[Span]:
        for m in self.pattern.finditer(text):
            s, e = m.span(self.group)
            if s < 0:
                continue
            if self.trim_url:
                e = s + len(trim_trailing(text[s:e]))
            if e <= s:
                continue
            value = text[s:e]
            ctx = self._context_hit(text, s, e)
            if self.context_required and not ctx:
                continue
            score, validated = self.score, None
            if self.validator is not None:
                if self.validator(value):
                    validated = True if self.hard_evidence else None
                elif self.demote_on_fail and ctx:
                    score, validated = 0.55, False
                else:
                    continue
            if ctx and self.context_boost:
                score = min(1.0, score + self.context_boost)
            label = self.label_fn(m) if self.label_fn else self.labels[0]
            yield Span(s, e, label, round(score, 3), SOURCE, validated)

    def _context_hit(self, text: str, s: int, e: int) -> bool:
        if self.context is not None and self.context.search(text[max(0, s - self.before):s]):
            return True
        return bool(self.context_after is not None and self.context_after.search(text[e:e + self.after]))


# ====================================================================== payment cards
class CardRecognizer:
    """PAN (Luhn + issuer brand) plus the expiry and security code that follow it.

    Candidate runs are split into digit groups and every group-aligned window of 13-19 digits
    is tested, so a stray leading number ("12 4242 4242 4242 4242") cannot hide a real PAN.
    Expiry/CVV found next to a validated PAN need no keyword; elsewhere they need one.
    """

    name = "payment_card"
    labels = ("CREDIT_CARD", "CVV", "CARD_EXPIRY")
    _PREFERRED = (16, 15, 14, 19, 18, 17, 13)

    def scan(self, text: str) -> Iterable[Span]:
        for run in P.CARD_RUN.finditer(text):
            if run.end() - run.start() >= 13:
                yield from self._scan_run(text, run)

    def _scan_run(self, text: str, run: re.Match):
        groups = [(run.start() + g.start(), run.start() + g.end(), g.group())
                  for g in re.finditer(r"\d+", run.group())]
        i = 0
        while i < len(groups):
            hit = self._best_window(text, groups, i)
            if hit is None:
                i += 1
                continue
            j, digits, brand, keyword = hit
            start, end = groups[i][0], groups[j][1]
            yield Span(start, end, "CREDIT_CARD", 0.98 if brand else 0.85, SOURCE, True)
            yield from self._adjacent(text, end, brand)
            i = j + 1

    def _best_window(self, text, groups, i):
        branded, plain, acc = {}, {}, ""
        for j in range(i, len(groups)):
            acc += groups[j][2]
            if len(acc) > 19:
                break
            if len(acc) >= 13 and V.luhn_valid(acc):
                brand = V.card_brand(acc)
                (branded if brand else plain)[len(acc)] = (j, acc, brand)
        for length in self._PREFERRED:
            if length in branded:
                j, acc, brand = branded[length]
                return j, acc, brand, False
        if plain and P.CARD_CTX.search(text[max(0, groups[i][0] - 40):groups[i][0]]):
            for length in self._PREFERRED:
                if length in plain:
                    j, acc, _ = plain[length]
                    return j, acc, None, True
        return None

    def _adjacent(self, text: str, end: int, brand):
        cvv_from = end
        exp = P.CARD_ADJ_EXP.match(text, end)
        if exp and V.expiry_valid(exp.group("v")):
            yield Span(exp.start("v"), exp.end("v"), "CARD_EXPIRY", 0.92, SOURCE, None)
            cvv_from = exp.end("v")
        cvv = P.CARD_ADJ_CVV.match(text, cvv_from)
        if cvv:
            code = cvv.group("v")
            expected = 4 if brand == "amex" else 3
            if cvv.group("kw") or (exp and len(code) == expected):
                yield Span(cvv.start("v"), cvv.end("v"), "CVV", 0.92, SOURCE, None)


# ====================================================================== IBAN
class IbanRecognizer:
    """Finds the country+check-digit header, then consumes exactly the registry length for
    that country (single spaces/hyphens allowed), so trailing words never poison mod-97."""

    name = "iban"
    labels = ("IBAN",)

    def scan(self, text: str) -> Iterable[Span]:
        for m in P.IBAN_START.finditer(text):
            expected = V.IBAN_LENGTHS.get(m.group("cc").upper())
            if expected is None:
                continue
            s = m.start()
            end = self._consume(text, s, expected)
            if end is None:
                continue
            candidate = text[s:end]
            if V.iban_valid(candidate):
                yield Span(s, end, "IBAN", 0.98, SOURCE, True)
            elif P.IBAN_CTX.search(text[max(0, s - 16):s]):
                yield Span(s, end, "IBAN", 0.55, SOURCE, False)

    @staticmethod
    def _consume(text: str, start: int, expected: int) -> Optional[int]:
        i, count, n = start, 0, len(text)
        while i < n and count < expected:
            c = text[i]
            if c.isascii() and c.isalnum():
                count += 1
                i += 1
            elif c in " -" and count and i + 1 < n and text[i + 1].isascii() and text[i + 1].isalnum():
                i += 1
            else:
                return None
        if count != expected or (i < n and text[i].isascii() and text[i].isalnum()):
            return None
        return i


# ====================================================================== secrets
class AwsSecretRecognizer:
    """A bare 40-char base64 string is only a secret when an AWS key id sits next to it."""

    name = "aws_secret_proximity"
    labels = ("CLOUD_SECRET",)

    def scan(self, text: str) -> Iterable[Span]:
        for key in P.AWS_KEY_ID.finditer(text):
            lo = max(0, key.start() - 200)
            for m in P.B64_40.finditer(text[lo:key.end() + 200]):
                v = m.group()
                if (V.shannon_entropy(v) >= 3.8 and any(c.isupper() for c in v)
                        and any(c.islower() for c in v) and not V._PLACEHOLDER.match(v)):
                    yield Span(lo + m.start(), lo + m.end(), "CLOUD_SECRET", 0.9, SOURCE, None)


class RecoveryCodeRecognizer:
    """`backup codes:` followed by a list of code-shaped tokens; each code is its own span."""

    name = "recovery_codes"
    labels = ("RECOVERY_CODE",)
    _MAX_CODES = 12

    def scan(self, text: str) -> Iterable[Span]:
        for head in P.RECOVERY_HEAD.finditer(text):
            pos = head.end()
            for _ in range(self._MAX_CODES):
                m = P.RECOVERY_TOKEN.match(text, pos)
                if not m:
                    break
                yield Span(m.start("c"), m.end("c"), "RECOVERY_CODE", 0.9, SOURCE, None)
                pos = m.end()
