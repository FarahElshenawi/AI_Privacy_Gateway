"""Tier1Engine: the Tier 1 entry point for the parallel detection pipeline.

    spans = Tier1Engine().scan(text)      # list[Span], offsets refer to `text`

Normalises first (NFKC, zero-width removal, dash folding) so obfuscated input can't hide a
value, scans with every recognizer, then maps spans back to ORIGINAL offsets so masking
edits the real text. Stateless after construction, therefore thread-safe. Overlap
resolution is NOT done here; the MergeEngine owns that.
"""
from __future__ import annotations

import unicodedata
from dataclasses import replace
from typing import Optional, Sequence

from ..span import Span
from .config import Tier1Config
from .recognizers import Recognizer
from .registry import build_default_recognizers

_DROP = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")
_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}


def _normalize(text: str) -> tuple[str, Optional[list[int]]]:
    """Returns (normalized, index) where index[i] = original offset of normalized char i.
    Pure-ASCII text takes a fast path (index=None, identity mapping)."""
    if text.isascii():
        return text, None
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch in _DROP:
            continue
        for c in unicodedata.normalize("NFKC", ch).translate(_DASHES):
            out.append(c)
            index.append(i)
    return "".join(out), index


class Tier1Engine:
    name = "tier1"

    def __init__(self, config: Optional[Tier1Config] = None,
                 extra_recognizers: Sequence[Recognizer] = ()) -> None:
        self.config = config or Tier1Config()
        self.recognizers: list[Recognizer] = build_default_recognizers(self.config) + list(extra_recognizers)

    @property
    def labels(self) -> frozenset[str]:
        return frozenset(l for r in self.recognizers for l in r.labels)

    def scan(self, text: str) -> list[Span]:
        if len(text) > self.config.max_text_len:
            raise ValueError(f"text longer than max_text_len={self.config.max_text_len}; chunk it first")
        normalized, index = _normalize(text)

        best: dict[tuple[int, int, str], Span] = {}   # collapse exact duplicates, keep strongest
        for rec in self.recognizers:
            for sp in rec.scan(normalized):
                key = (sp.start, sp.end, sp.label)
                old = best.get(key)
                if old is None or (sp.validated is True, sp.score) > (old.validated is True, old.score):
                    best[key] = sp

        spans = list(best.values())
        if index is not None:
            spans = [replace(sp, start=index[sp.start], end=index[sp.end - 1] + 1) for sp in spans]
        spans.sort(key=lambda s: (s.start, -s.end, s.label))
        return spans
