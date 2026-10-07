"""SegmentMasker: mask many text segments (pages, paragraphs, cells) by OFFSET.

Files are not one string. A segment is the smallest unit a file format lets us write back
(a paragraph, a cell, a page). This module:

  1. packs segments into batches (so a model tier runs once per ~100 KB, not once per cell),
  2. runs detection on each batch,
  3. clips every merged span to the segment(s) it lands in (a wrapped card number can
     straddle two cells),
  4. masks each segment with the OffsetMasker (shared vault, so one person = one fake
     across the whole file),
  5. returns EDITS per segment: (start, end, replacement) in the segment's own coordinates.

Handlers apply edits exactly where they were found. Nothing is ever replaced by searching
for a string, so "123" inside "12345" and "John" inside "Johnson" are untouched.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field, replace
from typing import Callable, Hashable, Optional, Sequence

from .detection import DetectionResult, DetectorReport
from .masker import OffsetMasker
from .merge import MergedSpan


class DetectionBlocked(Exception):
    """A critical detector failed for a batch. The file must not be sent."""

    def __init__(self, reports: Sequence[DetectorReport]) -> None:
        super().__init__("critical detector failed")
        self.reports = tuple(reports)


@dataclass(frozen=True, slots=True)
class Segment:
    key: Hashable
    text: str


@dataclass(frozen=True, slots=True)
class Edit:
    start: int
    end: int
    replacement: str
    label: str


@dataclass(slots=True)
class SegmentMaskResult:
    edits: dict = field(default_factory=dict)            # key -> tuple[Edit, ...] (sorted, disjoint)
    masked: dict = field(default_factory=dict)           # key -> masked text
    spans_masked: int = 0
    degraded: bool = False
    uncovered_labels: frozenset = frozenset()
    reports: tuple = ()                                  # DetectorReport of the worst batch per detector

    def __repr__(self) -> str:                           # never expose text
        return f"SegmentMaskResult(segments={len(self.masked)}, spans={self.spans_masked}, degraded={self.degraded})"


def apply_edits(text: str, edits: Sequence[Edit]) -> str:
    """Right-to-left splice; edits must be sorted and disjoint."""
    parts: list[str] = []
    cursor = len(text)
    for e in reversed(edits):
        parts.append(text[e.end:cursor])
        parts.append(e.replacement)
        cursor = e.start
    parts.append(text[:cursor])
    parts.reverse()
    return "".join(parts)


def apply_edits_to_runs(run_texts: Sequence[str], edits: Sequence[Edit]) -> list[str]:
    """Map paragraph-level edits back onto formatting runs.

    The replacement goes into the run that contains the edit's first character (so it keeps
    that run's formatting); the covered characters in every run are removed. Runs the edits
    don't touch come back identical, so callers can skip rewriting them.
    """
    out: list[str] = []
    pos = 0
    for t in run_texts:
        rs, re_ = pos, pos + len(t)
        pos = re_
        buf: list[str] = []
        cur = rs
        for e in edits:
            if e.end <= rs:
                continue
            if e.start >= re_:
                break
            lo, hi = max(e.start, rs), min(e.end, re_)
            buf.append(t[cur - rs:lo - rs])
            if rs <= e.start < re_:
                buf.append(e.replacement)
            cur = hi
        buf.append(t[cur - rs:])
        out.append("".join(buf))
    return out


def _split_long(text: str, limit: int) -> list[tuple[int, str]]:
    """Split at the last newline/space inside the window so tokens stay whole."""
    if len(text) <= limit:
        return [(0, text)]
    pieces, pos = [], 0
    while pos < len(text):
        end = min(len(text), pos + limit)
        if end < len(text):
            cut = max(text.rfind("\n", pos, end), text.rfind(" ", pos, end))
            if cut > pos:
                end = cut + 1
        pieces.append((pos, text[pos:end]))
        pos = end
    return pieces


class SegmentMasker:
    def __init__(self, detect: Callable[[str], DetectionResult], masker: OffsetMasker, *,
                 max_batch_chars: int = 100_000) -> None:
        self._detect = detect
        self._masker = masker
        self._max = max_batch_chars

    def mask_segments(self, segments: Sequence[Segment], conversation_id: str) -> SegmentMaskResult:
        # flatten into pieces: (segment index, offset inside segment, text)
        pieces: list[tuple[int, int, str]] = []
        for i, seg in enumerate(segments):
            if not seg.text.strip():
                continue
            for off, txt in _split_long(seg.text, self._max):
                pieces.append((i, off, txt))

        edits: dict[Hashable, list[Edit]] = {}
        degraded = False
        uncovered: set[str] = set()
        worst: dict[str, DetectorReport] = {}
        total = 0

        for batch in self._batches(pieces):
            joined = "\n".join(p[2] for p in batch)
            starts, pos = [], 0
            for _, _, txt in batch:
                starts.append(pos)
                pos += len(txt) + 1
            result = self._detect(joined)
            if result.blocked:
                raise DetectionBlocked(result.reports)
            degraded |= result.degraded
            uncovered |= set(result.uncovered_labels)
            for r in result.reports:
                if r.name not in worst or r.status.value != "ok":
                    worst[r.name] = r

            per_piece: list[list[MergedSpan]] = [[] for _ in batch]
            for ms in result.merged:
                k = max(0, bisect.bisect_right(starts, ms.start) - 1)
                while k < len(batch) and starts[k] < ms.end:
                    p_start, p_len = starts[k], len(batch[k][2])
                    s, e = max(ms.start, p_start) - p_start, min(ms.end, p_start + p_len) - p_start
                    if e > s:
                        per_piece[k].append(replace(ms, start=s, end=e))
                    k += 1

            for (seg_i, off, txt), spans in zip(batch, per_piece):
                if not spans:
                    continue
                res = self._masker.mask(txt, spans, conversation_id)
                key = segments[seg_i].key
                for info in res.spans:
                    edits.setdefault(key, []).append(Edit(
                        info.start + off, info.end + off,
                        res.masked_text[info.masked_start:info.masked_end], info.label))
                    total += 1

        out = SegmentMaskResult(spans_masked=total, degraded=degraded,
                                uncovered_labels=frozenset(uncovered), reports=tuple(worst.values()))
        for seg in segments:
            es = tuple(sorted(edits.get(seg.key, ()), key=lambda e: e.start))
            out.edits[seg.key] = es
            out.masked[seg.key] = apply_edits(seg.text, es) if es else seg.text
        return out

    def _batches(self, pieces: list[tuple[int, int, str]]):
        batch, size = [], 0
        for p in pieces:
            if batch and size + len(p[2]) + 1 > self._max:
                yield batch
                batch, size = [], 0
            batch.append(p)
            size += len(p[2]) + 1
        if batch:
            yield batch
