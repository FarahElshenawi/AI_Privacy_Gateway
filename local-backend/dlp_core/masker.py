"""OffsetMasker: apply MergedSpans to text by offset, never by string search.

Why this removes the old bug classes:
  * Cascading replacement: every replacement is computed from the ORIGINAL
    text slice at known offsets and spliced in once. Replacement text is never
    scanned again, so a fake can't be re-masked by a later rule.
  * "John" vs "Johnson": only the exact offsets chosen by the merge engine are
    touched. An occurrence that no detector flagged is never modified, and a
    partial detection was already widened to the whole token upstream.
  * Restore side: `Demasker` swaps fakes back in ONE regex pass, longest
    first, with word-boundary guards, so "James" never rewrites "James Walsh"
    or "Jamesville".

Fail-closed behaviour:
  * Invalid, unsorted or overlapping spans -> MaskingError (nothing is sent).
  * FAKER span with no provider, or no collision-free fake after N tries ->
    falls back to REDACT. A leak is never the fallback.
  * BLOCK span -> raises RequestBlockedError immediately before any vault writes.
  * REDACT + STORE_FOR_DEMASKING generates unique [[REDACTED:LABEL:xxxx]] placeholder
    and stores it in the vault so bijective Demasker can restore it.
  * REDACT + NO_STORE uses [REDACTED:{label}] or [[REDACTED]] with zero vault retention.
  * Audit records emitted via emit_audit() with zero secret leakage.
  * MaskResult never contains real values; they go only to the sealed vault.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .audit import build_audit_record, emit_audit
from .merge import MergedSpan
from .policy import Action, StoragePolicy, route_label
from .span import Evidence
from .vault import InMemoryVault, VaultCollisionError

SurrogateProvider = Callable[[str, str], str]   # (label, real_value) -> candidate fake


class MaskingError(Exception):
    pass


class RequestBlockedError(Exception):
    """A BLOCK decision: the request must not be sent to the external LLM."""

    def __init__(self, entity_type: str, rule: str):
        super().__init__(f"request blocked by policy ({entity_type}, {rule})")
        self.entity_type = entity_type
        self.rule = rule


@dataclass(frozen=True, slots=True)
class MaskedSpanInfo:
    """Audit record for one masked region. Contains NO real value."""
    label: str
    action: Action               # action actually applied (FAKER may degrade to REDACT)
    start: int                   # offsets in the original text
    end: int
    masked_start: int            # offsets in the masked text
    masked_end: int
    evidence: Evidence
    reused_surrogate: bool


@dataclass(frozen=True, slots=True)
class MaskResult:
    masked_text: str
    spans: tuple[MaskedSpanInfo, ...]

    def __repr__(self) -> str:
        return f"MaskResult(len={len(self.masked_text)}, spans={len(self.spans)})"


class OffsetMasker:
    def __init__(
        self,
        vault: InMemoryVault,
        surrogate: Optional[SurrogateProvider] = None,
        *,
        redaction_template: str = "[REDACTED:{label}]",
        max_attempts: int = 25,
    ) -> None:
        self._vault = vault
        self._surrogate = surrogate
        self._template = redaction_template
        self._max_attempts = max_attempts

    def mask(self, text: str, spans: Sequence[MergedSpan], conversation_id: str) -> MaskResult:
        self._validate(text, spans)

        # Pass 1: Check for BLOCK decisions and emit audit logs.
        # If ANY span resolves to BLOCK, raise RequestBlockedError immediately
        # before any vault write or token emission happens.
        for ms in spans:
            real = text[ms.start:ms.end]
            decision = route_label(ms.label, real)
            emit_audit(build_audit_record(decision, conversation_id, len(real)))
            if ms.action is Action.BLOCK or decision.action is Action.BLOCK:
                raise RequestBlockedError(decision.label, decision.rule)

        # Pass 2: Decide replacements honoring StoragePolicy and Actions.
        reps: list[str] = []
        applied: list[tuple[Action, bool]] = []
        for ms in spans:
            real = text[ms.start:ms.end]
            decision = route_label(ms.label, real)

            if ms.action is Action.FAKER:
                got = self._fake_for(text, conversation_id, ms.label, real)
                if got is not None:
                    reps.append(got[0])
                    applied.append((Action.FAKER, got[1]))
                    continue
                # Degradation fallback: FAKER with no fake -> REDACT
                # Fall through to redaction handling below

            # Action.REDACT or degraded FAKER:
            # Stored redaction applies when the entity was originally a REDACT action
            # that is configured to be stored (e.g. IBAN, INTERNAL_URL).
            # A degraded FAKER (e.g. PERSON when Faker fails) simply falls back to the template.
            if ms.action is Action.REDACT and decision.storage is StoragePolicy.STORE_FOR_DEMASKING:
                # Need unique placeholder stored in vault so bijective demasking works
                placeholder, reused = self._unique_redaction_for(conversation_id, ms.label, real)
                reps.append(placeholder)
                applied.append((Action.REDACT, reused))
            else:
                # NO_STORE or degraded FAKER: plain template, zero vault entry
                reps.append(self._template.format(label=ms.label))
                applied.append((Action.REDACT, False))

        # Right-to-left assembly in one pass: untouched tails and replacements are
        # collected from the end, then reversed. O(n) and offset-stable.
        parts: list[str] = []
        cursor = len(text)
        for ms, rep in zip(reversed(spans), reversed(reps)):
            parts.append(text[ms.end:cursor])
            parts.append(rep)
            cursor = ms.start
        parts.append(text[:cursor])
        parts.reverse()
        masked = "".join(parts)

        # Masked-side offsets for the audit trail.
        infos: list[MaskedSpanInfo] = []
        delta = 0
        for ms, rep, (act, reused) in zip(spans, reps, applied):
            ms_start = ms.start + delta
            infos.append(MaskedSpanInfo(ms.label, act, ms.start, ms.end,
                                        ms_start, ms_start + len(rep), ms.evidence, reused))
            delta += len(rep) - (ms.end - ms.start)
        return MaskResult(masked, tuple(infos))

    # -- internals -------------------------------------------------------
    @staticmethod
    def _validate(text: str, spans: Sequence[MergedSpan]) -> None:
        prev_end = 0
        for ms in spans:
            if not (0 <= ms.start < ms.end <= len(text)):
                raise MaskingError(f"span [{ms.start},{ms.end}) out of bounds for text of {len(text)}")
            if ms.start < prev_end:
                raise MaskingError("spans must be sorted and non-overlapping (run MergeEngine first)")
            prev_end = ms.end

    def _fake_for(self, text: str, conv: str, label: str, real: str) -> Optional[tuple[str, bool]]:
        existing = self._vault.lookup_fake(conv, label, real)
        if existing is not None:
            return existing, True
        if self._surrogate is None:
            return None
        for _ in range(self._max_attempts):
            cand = self._surrogate(label, real)
            # A fake that equals the real value, or already occurs in the text,
            # would make restoration ambiguous or leak the value.
            if not cand or cand == real or cand in text:
                continue
            if self._vault.fake_in_use(conv, cand):
                continue
            try:
                self._vault.put(conv, label, real, cand)
            except VaultCollisionError:
                continue
            return cand, False
        return None

    def _unique_redaction_for(self, conv: str, label: str, real: str) -> tuple[str, bool]:
        existing = self._vault.lookup_fake(conv, label, real)
        if existing is not None:
            return existing, True
        # Generate unique [[REDACTED:LABEL:token]] candidate
        while True:
            candidate = f"[[REDACTED:{label}:{secrets.token_hex(2)}]]"
            if not self._vault.fake_in_use(conv, candidate):
                try:
                    self._vault.put(conv, label, real, candidate)
                    return candidate, False
                except VaultCollisionError:
                    continue


class Demasker:
    """Restore real values in an LLM response: one pass, longest fake first."""

    def __init__(self, vault: InMemoryVault) -> None:
        self._vault = vault
        self._cache: dict[str, tuple[int, Optional[re.Pattern[str]], dict[str, str]]] = {}

    def restore(self, text: str, conversation_id: str) -> tuple[str, int]:
        pattern, lookup = self._compiled(conversation_id)
        if pattern is None:
            return text, 0
        count = 0

        def repl(m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return lookup[m.group(0)]

        return pattern.sub(repl, text), count

    def _compiled(self, conv: str) -> tuple[Optional[re.Pattern[str]], dict[str, str]]:
        version = self._vault.version(conv)
        hit = self._cache.get(conv)
        if hit is not None and hit[0] == version:
            return hit[1], hit[2]
        pairs = self._vault.items(conv)
        lookup = dict(pairs)
        if not lookup:
            self._cache[conv] = (version, None, {})
            return None, {}
        alts = []
        for fake in sorted(lookup, key=len, reverse=True):
            # If fake starts/ends with special bracket chars like '[', boundary check is not \w
            left = r"(?<!\w)" if (fake[0].isalnum() or fake[0] == "_") else ""
            right = r"(?!\w)" if (fake[-1].isalnum() or fake[-1] == "_") else ""
            alts.append(f"{left}{re.escape(fake)}{right}")
        pattern = re.compile("|".join(alts))
        self._cache[conv] = (version, pattern, lookup)
        return pattern, lookup
