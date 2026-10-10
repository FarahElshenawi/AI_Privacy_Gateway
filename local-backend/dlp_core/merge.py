"""MergeEngine: many overlapping detector spans -> disjoint spans to mask.

Guarantees (these are the invariants the tests fuzz):
  1. Coverage: every character covered by ANY non-KEEP input span is covered
     by exactly one output span. Overlaps are resolved by strict union, never
     by dropping the shorter span, so no tail of a span is left in the clear.
  2. Output is sorted, non-overlapping, in bounds.
  3. Label conflicts resolve to the strictest action (REDACT > FAKER).
  4. Within the strictest action, evidence decides:
     VALIDATED > CONTEXT > MODEL, then LENGTH (the container wins: a connection string
     beats the IP inside it), then score, then label name (determinism only).

Design notes:
  * KEEP spans are discarded before clustering. KEEP means "this label is not
    sensitive", so it must not widen or veto a sensitive span.
  * Touching spans ([0,5) and [5,9)) are NOT merged; they share no character.
  * Optional word-boundary snapping widens a span to the whole alphanumeric
    token it cuts into ("John" inside "Johnson" -> "Johnson"), so a partial
    detection can never leave a fragment of the token behind.
  * O(n log n) for the sort, then one linear sweep.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from .policy import Action, Policy
from .span import Evidence, Span


@dataclass(frozen=True, slots=True)
class MergedSpan:
    """A final, disjoint region to mask, plus how the decision was reached."""
    start: int
    end: int
    label: str                 # label of the winning member
    action: Action             # strictest action in the cluster
    score: float               # score of the winning member
    evidence: Evidence         # evidence of the winning member
    sources: tuple[str, ...]   # every detector that contributed
    labels: tuple[str, ...]    # every label that contributed (audit)

    @property
    def length(self) -> int:
        return self.end - self.start


def _is_word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _snap(start: int, end: int, text: str) -> tuple[int, int]:
    """Widen [start, end) to token boundaries where it cuts through a token."""
    n = len(text)
    while start > 0 and _is_word(text[start - 1]) and _is_word(text[start]):
        start -= 1
    while end < n and _is_word(text[end]) and _is_word(text[end - 1]):
        end += 1
    return start, end


class MergeEngine:
    def __init__(
        self,
        policy: Optional[Policy] = None,
        *,
        snap_to_word_boundary: bool = True,
        min_scores: Optional[Mapping[str, float]] = None,
    ) -> None:
        """
        Args:
            policy: label -> action table (default: DEFAULT_ACTIONS, unknown=REDACT).
            snap_to_word_boundary: widen spans that cut through a token. Only
                applies when `text` is passed to merge().
            min_scores: optional per-label floor for NON-validated spans.
                Validated spans always pass. Default: no floor (fail closed).
        """
        self._policy = policy or Policy()
        self._snap = snap_to_word_boundary
        self._min = {k.upper(): float(v) for k, v in (min_scores or {}).items()}

    def set_policy(self, policy: Policy) -> None:
        """Atomically swap the label->action table (Policy is immutable after construction)."""
        self._policy = policy

    def set_min_scores(self, min_scores: Optional[Mapping[str, float]]) -> None:
        """Replace the per-label floors (a new dict is swapped in, so merge() never sees a half-update)."""
        self._min = {k.upper(): float(v) for k, v in (min_scores or {}).items()}

    def merge(self, spans: Iterable[Span], text: Optional[str] = None) -> list[MergedSpan]:
        n = len(text) if text is not None else None
        items: list[tuple[int, int, Span, Action]] = []

        for sp in spans:
            if not isinstance(sp, Span):
                raise TypeError(f"detectors must emit Span, got {type(sp).__name__}")
            if n is not None and sp.end > n:
                # A detector disagreeing with the text is a bug; fail closed.
                raise ValueError(
                    f"span [{sp.start},{sp.end}) from {sp.source!r} exceeds text length {n}")
            val = text[sp.start:sp.end] if text is not None else None
            action = self._policy.action_for(sp.label, val)
            if action is Action.KEEP:
                continue
            floor = self._min.get(sp.label)
            if floor is not None and sp.evidence is not Evidence.VALIDATED and sp.score < floor:
                continue
            start, end = sp.start, sp.end
            if self._snap and text is not None:
                start, end = _snap(start, end, text)
            items.append((start, end, sp, action))

        if not items:
            return []

        items.sort(key=lambda t: (t[0], -t[1]))

        out: list[MergedSpan] = []
        members: list[tuple[Span, Action]] = [(items[0][2], items[0][3])]
        c_start, c_end = items[0][0], items[0][1]

        for start, end, sp, action in items[1:]:
            if start < c_end:                       # overlaps current cluster: union
                members.append((sp, action))
                if end > c_end:
                    c_end = end
            else:
                out.append(self._finish(c_start, c_end, members))
                members = [(sp, action)]
                c_start, c_end = start, end
        out.append(self._finish(c_start, c_end, members))
        return out

    @staticmethod
    def _finish(start: int, end: int, members: list[tuple[Span, Action]]) -> MergedSpan:
        strictest = max(a for _, a in members)
        pool = [m for m, a in members if a == strictest]
        winner = max(pool, key=lambda s: (s.evidence, s.length, s.score, s.label))
        return MergedSpan(
            start=start,
            end=end,
            label=winner.label,
            action=strictest,
            score=winner.score,
            evidence=winner.evidence,
            sources=tuple(sorted({m.source for m, _ in members})),
            labels=tuple(sorted({m.label for m, _ in members})),
        )
