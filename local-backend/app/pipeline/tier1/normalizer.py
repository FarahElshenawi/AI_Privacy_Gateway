"""Stage 0 normalization with an offset map back to the original text.

Why: obfuscated input defeats pattern matching (zero-width characters inside a card number,
fullwidth digits, en-dashes instead of hyphens). We normalize for detection, then map every
span back to original offsets so masking edits the real text.
"""
from __future__ import annotations

import unicodedata
from dataclasses import replace

from .types import Span

_DROP = frozenset("\u200b\u200c\u200d\u2060\ufeff\u00ad")
_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}


class TextNormalizer:
    def normalize(self, text: str):
        """Returns (normalized_text, index) where index[i] is the original offset of char i.
        Pure-ASCII text takes a fast path with index=None (identity mapping)."""
        if text.isascii():
            return text, None
        out, index = [], []
        for i, ch in enumerate(text):
            if ch in _DROP:
                continue
            for c in unicodedata.normalize("NFKC", ch).translate(_DASHES):
                out.append(c)
                index.append(i)
        return "".join(out), index

    @staticmethod
    def map_span(span: Span, index) -> Span:
        if index is None or not span.end > span.start:
            return span
        return replace(span, start=index[span.start], end=index[span.end - 1] + 1)
