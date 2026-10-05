"""Shared types for the Tier 1 detection engine.

Span is the only type every module needs — it's the unit of work that flows
from recognizers → engine → merge_engine → masking.

We use a frozen dataclass so spans are hashable (needed for dedup sets) and
safe to pass across threads.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Span:
    """A detected entity span in the text.

    Fields:
        start:      0-indexed character offset (inclusive) in the scanned text.
        end:        0-indexed character offset (exclusive).
        label:      Entity type, e.g. "CREDIT_CARD", "EMAIL", "IBAN".
        score:      Precision prior (0.0–1.0). NOT a probability — it's the
                    expected precision of this recognizer on this label,
                    calibrated on the hold-out set.
        source:     Which tier produced this span. "regex" for Tier 1,
                    "gliner" for Tier 2, "spanmarker" for Tier 3.
        validated:  None  = not checked (plausibility only)
                    True  = checksum/structural proof (Luhn, mod-97, etc.)
                    False = validator ran and FAILED (low confidence, context
                            may still salvage it)
    """
    start: int
    end: int
    label: str
    score: float = 0.9
    source: str = "regex"
    validated: Optional[bool] = None

    @property
    def text(self) -> str:
        """The matched text — set by the engine after scanning, not by recognizers.
        Recognizers don't have access to the text at Span construction time in
        a streaming context, so this is populated downstream."""
        return getattr(self, "_text", "")

    def with_text(self, text: str) -> "Span":
        """Return a copy with the text attached (for debugging/logging)."""
        # frozen dataclass — can't set _text directly, use object.__setattr__
        import copy
        s = copy.copy(self)
        object.__setattr__(s, "_text", text)
        return s

    def __repr__(self) -> str:
        v = "" if self.validated is None else f" validated={self.validated}"
        return f"Span({self.start}:{self.end} {self.label!r} score={self.score}{v})"
