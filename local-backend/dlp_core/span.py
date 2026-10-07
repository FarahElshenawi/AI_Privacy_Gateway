"""The unified detector output contract.

Every detector (Tier 1 regex, Tier 2 GLiNER, Tier 3 SpanMarker, tenant rules)
emits `Span` objects and nothing else. Offsets are half-open character
offsets, [start, end), into the exact text string that was scanned.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class Evidence(IntEnum):
    """Strength of evidence behind a span. Higher wins in the merge engine.

    VALIDATED: a hard check passed (Luhn, mod-97, ABA, base58check, ...).
    CONTEXT:   a keyword/adjacency rule anchored the match.
    MODEL:     only a model score or a bare pattern supports it.
    """
    MODEL = 0
    CONTEXT = 1
    VALIDATED = 2


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class Span:
    """A detected sensitive region.

    Attributes:
        start:     inclusive start offset (>= 0).
        end:       exclusive end offset (> start).
        label:     entity type; normalised to upper case ("CREDIT_CARD").
        score:     detector confidence/precision prior in [0, 1]. Not a
                   calibrated probability; only comparable within a label.
        source:    detector id for provenance ("regex", "gliner", ...).
        validated: True  = a hard validator passed,
                   False = a validator ran and failed,
                   None  = no validator applies.
        context:   True if a keyword/adjacency rule anchored this match.
    """
    start: int
    end: int
    label: str
    score: float = 0.5
    source: str = "unknown"
    validated: Optional[bool] = None
    context: bool = False

    def __post_init__(self) -> None:
        if not (_is_int(self.start) and _is_int(self.end)):
            raise TypeError("Span offsets must be int")
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"invalid span range [{self.start}, {self.end})")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Span label must be a non-empty string")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)) \
                or not 0.0 <= float(self.score) <= 1.0:  # also rejects NaN
            raise ValueError(f"score must be in [0, 1], got {self.score!r}")
        if self.validated not in (True, False, None):
            raise TypeError("validated must be True, False or None")
        object.__setattr__(self, "label", self.label.strip().upper())
        object.__setattr__(self, "score", float(self.score))

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def evidence(self) -> Evidence:
        if self.validated is True:
            return Evidence.VALIDATED
        if self.context:  # includes "validator failed but context salvaged it"
            return Evidence.CONTEXT
        return Evidence.MODEL

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end
