"""Recognizers: glue between patterns.py (candidates) and validators.py (verdicts).

Every recognizer implements one method, `scan(text) -> Iterator[Span]`, and returns the
unified `dlp_core.Span`. Evidence flags follow the contract the MergeEngine ranks on:

    validated=True   a hard check passed (Luhn, mod-97, ABA, base58check, JWT structure, ...)
    validated=False  a validator ran and FAILED, but a cue kept the span at low score
    context=True    a cue keyword / adjacency rule anchored the match
    neither          bare pattern evidence only (MODEL level)

Two kinds:
  * PatternRecognizer: declarative. One regex + optional validator + optional cue rule.
    Adding a new regex/validator combo is one registry line (see registry.py).
  * Custom recognizers where one regex match cannot judge a candidate (card windows,
    IBAN length trimming, key/secret proximity, code lists).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Protocol, Union, runtime_checkable

from ..span import Span
from . import patterns as P
from . import validators as V

_TRAIL = ".,;:!?)]}'\"`"


def trim_trailing(value: str) -> str:
    return value.rstrip(_TRAIL)


def _src(name: str) -> str:
    return f"tier1.{name}"


@runtime_checkable
class Recognizer(Protocol):
    # Read-only properties, so both dataclass fields and plain class attributes
    # (`labels = ("IBAN",)`, inferred as tuple[str]) satisfy the protocol.
    @property
    def name(self) -> str: ...

    @property
    def labels(self) -> tuple[str, ...]: ...

    def scan(self, text: str) -> Iterator[Span]: ...


# ====================================================================== declarative
@dataclass(frozen=True, slots=True)
class PatternRecognizer:
    name: str
    labels: tuple[str, ...]
    pattern: re.Pattern
    group: Union[int, str] = 0
    label_fn: Optional[Callable[[re.Match], str]] = None
    validator: Optional[Callable[[str], bool]] = None
    hard_evidence: bool = False        # validator pass == checksum-grade proof -> validated=True
    score: float = 0.9
    anchored: bool = False             # the pattern itself embeds the cue keyword -> context=True
    context: Optional[re.Pattern] = None         # cue searched in the text BEFORE the value
    context_after: Optional[re.Pattern] = None   # cue searched in the text AFTER the value
    context_required: bool = False     # no cue, no span
    reject_before: Optional[re.Pattern] = None   # text just BEFORE the value that disqualifies it
    reject_window: int = 140
    demote_on_fail: bool = False       # validator failed but cue present: keep at low score
    trim_url: bool = False
    before: int = 40
    after: int = 24

    def scan(self, text: str) -> Iterator[Span]:
        src = _src(self.name)
        for m in self.pattern.finditer(text):
            s, e = m.span(self.group)
            if s < 0:
                continue
            if self.trim_url:
                e = s + len(trim_trailing(text[s:e]))
            if e <= s:
                continue
            value = text[s:e]
            if self.reject_before is not None and self.reject_before.search(text[max(0, s - self.reject_window):s]):
                continue
            cued = self.anchored or self._cue_hit(text, s, e)
            if self.context_required and not cued:
                continue
            score, validated = self.score, None
            if self.validator is not None:
                if self.validator(value):
                    validated = True if self.hard_evidence else None
                elif self.demote_on_fail and cued:
                    score, validated = 0.55, False
                else:
                    continue
            label = self.label_fn(m) if self.label_fn else self.labels[0]
            yield Span(s, e, label, score, src, validated, cued)

    def _cue_hit(self, text: str, s: int, e: int) -> bool:
        if self.context is not None and self.context.search(text[max(0, s - self.before):s]):
            return True
        return bool(self.context_after is not None and self.context_after.search(text[e:e + self.after]))


# ====================================================================== payment cards
class CardRecognizer:
    """PAN (Luhn + issuer brand) plus the expiry and security code that follow it.

    A run of digit groups is tested window by window, so a stray leading number
    ("12 4242 4242 4242 4242") cannot hide a real PAN. A Luhn-valid number with no known
    brand needs a card word just before it. Expiry/CVV directly after a validated PAN need
    no keyword; everywhere else they need one (CVV_KEYWORD / EXPIRY_KEYWORD recognizers).
    """

    name = "payment_card"
    labels = ("CREDIT_CARD", "CVV", "CARD_EXPIRY")
    _PREFERRED = (16, 15, 14, 19, 18, 17, 13)

    def scan(self, text: str) -> Iterator[Span]:
        for run in P.CARD_RUN.finditer(text):
            if run.end() - run.start() >= 13:
                yield from self._scan_run(text, run)

    def _scan_run(self, text: str, run: re.Match) -> Iterator[Span]:
        groups = [(run.start() + g.start(), run.start() + g.end(), g.group())
                  for g in re.finditer(r"\d+", run.group())]
        src = _src(self.name)
        i = 0
        while i < len(groups):
            hit = self._best_window(text, groups, i)
            if hit is None:
                i += 1
                continue
            j, brand, carded = hit
            start, end = groups[i][0], groups[j][1]
            yield Span(start, end, "CREDIT_CARD", 0.98 if brand else 0.85, src, True, carded)
            yield from self._adjacent(text, end, brand, src)
            i = j + 1

    def _best_window(self, text: str, groups: list, i: int):
        branded: dict[int, tuple] = {}
        plain: dict[int, tuple] = {}
        acc = ""
        for j in range(i, len(groups)):
            acc += groups[j][2]
            if len(acc) > 19:
                break
            if len(acc) >= 13 and V.luhn_valid(acc):
                brand = V.card_brand(acc)
                (branded if brand else plain)[len(acc)] = (j, brand)
        for length in self._PREFERRED:
            if length in branded:
                j, brand = branded[length]
                return j, brand, False
        if plain and P.CARD_CTX.search(text[max(0, groups[i][0] - 40):groups[i][0]]):
            for length in self._PREFERRED:
                if length in plain:
                    return plain[length][0], None, True
        return None

    @staticmethod
    def _adjacent(text: str, end: int, brand: Optional[str], src: str) -> Iterator[Span]:
        cvv_from = end
        exp = P.CARD_ADJ_EXP.match(text, end)
        if exp and V.expiry_valid(exp.group("v")):
            yield Span(exp.start("v"), exp.end("v"), "CARD_EXPIRY", 0.92, src, None, True)
            cvv_from = exp.end("v")
        cvv = P.CARD_ADJ_CVV.match(text, cvv_from)
        if cvv:
            expected = 4 if brand == "amex" else 3
            code = cvv.group("v")
            if cvv.group("kw") or (exp and len(code) == expected):
                yield Span(cvv.start("v"), cvv.end("v"), "CVV", 0.92, src, None, True)


# ====================================================================== IBAN
class IbanRecognizer:
    """Finds the country + check-digit header, then consumes exactly the registry length for
    that country (single spaces/hyphens allowed), so trailing words never poison mod-97."""

    name = "iban"
    labels = ("IBAN",)

    def scan(self, text: str) -> Iterator[Span]:
        src = _src(self.name)
        for m in P.IBAN_START.finditer(text):
            expected = V.IBAN_LENGTHS.get(m.group("cc").upper())
            if expected is None:
                continue
            s = m.start()
            end = self._consume(text, s, expected)
            if end is None:
                continue
            if V.iban_valid(text[s:end]):
                yield Span(s, end, "IBAN", 0.98, src, True, False)
            elif P.IBAN_CTX.search(text[max(0, s - 16):s]):
                yield Span(s, end, "IBAN", 0.55, src, False, True)

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
    """A bare 40-char base64 string is a secret only when an AWS key id sits within 200 chars."""

    name = "aws_secret_proximity"
    labels = ("CLOUD_SECRET",)

    def scan(self, text: str) -> Iterator[Span]:
        src = _src(self.name)
        for key in P.AWS_KEY_ID.finditer(text):
            lo = max(0, key.start() - 200)
            for m in P.B64_40.finditer(text[lo:key.end() + 200]):
                v = m.group()
                if (V.shannon_entropy(v) >= 3.8 and any(c.isupper() for c in v)
                        and any(c.islower() for c in v) and not V.PLACEHOLDER.match(v)):
                    yield Span(lo + m.start(), lo + m.end(), "CLOUD_SECRET", 0.9, src, None, True)


class RecoveryCodeRecognizer:
    """`backup codes:` followed by code-shaped tokens; each code becomes its own span."""

    name = "recovery_codes"
    labels = ("RECOVERY_CODE",)
    _MAX_CODES = 12

    def scan(self, text: str) -> Iterator[Span]:
        src = _src(self.name)
        for head in P.RECOVERY_HEAD.finditer(text):
            pos = head.end()
            for _ in range(self._MAX_CODES):
                m = P.RECOVERY_TOKEN.match(text, pos)
                if not m:
                    break
                yield Span(m.start("c"), m.end("c"), "RECOVERY_CODE", 0.9, src, None, True)
                pos = m.end()


# ====================================================================== tenant terms
class DenyTermRecognizer:
    """Customer-configured words that must never leave (project names, client names)."""

    name = "deny_terms"
    labels = ("DENY_TERM",)

    def __init__(self, terms: tuple[str, ...]) -> None:
        clean = sorted({t.strip() for t in terms if t and t.strip()}, key=len, reverse=True)
        self._rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in clean) + r")(?!\w)",
                              re.IGNORECASE) if clean else None

    def scan(self, text: str) -> Iterator[Span]:
        if self._rx is None:
            return
        src = _src(self.name)
        for m in self._rx.finditer(text):
            yield Span(m.start(), m.end(), "DENY_TERM", 1.0, src, None, True)


# ====================================================================== ssh-style user@host
class SshUserRecognizer:
    """The USER part of user@host in an ssh / scp / sftp / mosh / rsync command line.

    GLiNER2 reads an e-mail-shaped word such as `deploy@db01.internal` as ONE word and so cannot mark the user
    alone; a deterministic rule can. Only tokens AFTER the command word on the same line (at most 200 chars) count,
    and shared service accounts (root, git, ubuntu, ...) are skipped."""
    name = "ssh_user"
    labels = ("USERNAME",)

    def scan(self, text: str) -> Iterator[Span]:
        src = _src(self.name)
        for cm in P.SSH_CMD.finditer(text):
            nl = text.find("\n", cm.end())
            stop = min(len(text) if nl < 0 else nl, cm.end() + 200)
            for m in P.USER_AT_HOST.finditer(text, cm.end(), stop):
                if m.group("u").lower() in P.SERVICE_ACCOUNTS:
                    continue
                yield Span(m.start("u"), m.end("u"), "USERNAME", 0.9, src, None, True)
