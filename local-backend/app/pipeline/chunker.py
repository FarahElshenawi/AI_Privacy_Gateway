"""Text chunker — splits long text at sentence boundaries for model inference.

GLiNER and similar models have a context window limit (512 tokens for
GLiNER). This module splits long text into overlapping chunks that respect
sentence boundaries — no sentence is ever cut in half.

Design decisions:
  1. Chunk at sentence boundaries (. ? ! \\n\\n) — keeps complete sentences
     intact so GLiNER gets full context for entity detection.
  2. Overlap: chunks overlap by ~200 chars so entities spanning a
     sentence boundary aren't lost.
  3. Offset remapping: entities found in a chunk have offsets adjusted
     to be relative to the full document, not the chunk.
  4. Deduplication: entities found in the overlap zone of two chunks
     are merged.
"""
from __future__ import annotations

import re


class TextChunker:
    """Splits long text into overlapping chunks at sentence boundaries.

    Usage:
        chunker = TextChunker(max_tokens=400, overlap_tokens=50)
        chunks = chunker.chunk(text)
        # chunks = [(start_offset, chunk_text), ...]

        # After running GLiNER on each chunk, remap offsets:
        for chunk_start, chunk_text in chunks:
            entities = gliner.detect(chunk_text)
            chunker.remap_offsets(entities, chunk_start)

        # Then merge all entities (dedup overlap zone):
        final_entities = chunker.merge_chunks(all_entities_per_chunk)
    """

    def __init__(self, max_tokens: int = 400, overlap_tokens: int = 50, chars_per_token: float = 4.0):
        """
        Args:
            max_tokens: max tokens per chunk (GLiNER limit is 512, we use 400 for safety).
            overlap_tokens: how many tokens of overlap between chunks.
            chars_per_token: approximate chars per token (English ~4, varies by language).
        """
        self.max_chars = int(max_tokens * chars_per_token)       # 1600
        self.overlap_chars = int(overlap_tokens * chars_per_token)  # 200
        self.stride = self.max_chars - self.overlap_chars           # 1400

    def chunk(self, text: str) -> list[tuple[int, str]]:
        """Split text into overlapping chunks at sentence boundaries.

        Returns:
            List of (start_offset_in_full_text, chunk_text) tuples.
            Offsets are 0-indexed character positions in the original text.
        """
        if len(text) <= self.max_chars:
            return [(0, text)]

        chunks: list[tuple[int, str]] = []
        start = 0

        while start < len(text):
            end = start + self.max_chars

            if end >= len(text):
                # Last chunk — take everything remaining
                chunks.append((start, text[start:]))
                break

            # Find the next sentence boundary at or after `end`
            boundary = self._find_sentence_boundary(text, end)

            chunk_text = text[start:boundary]
            chunks.append((start, chunk_text))

            # Next chunk starts with overlap — also at a sentence boundary
            overlap_start = boundary - self.overlap_chars
            # Find the next sentence boundary AFTER overlap_start so we don't
            # start mid-sentence in the overlap zone
            next_start = self._find_sentence_boundary(text, overlap_start, forward=True)

            # Safety: ensure we're making progress (avoid infinite loop)
            if next_start <= start:
                next_start = boundary

            start = next_start

        return chunks

    def _find_sentence_boundary(self, text: str, pos: int, forward: bool = False) -> int:
        """Find the nearest sentence boundary at or after position `pos`.

        Sentence boundaries are: . ! ? followed by space/newline, or \\n\\n.

        If `forward=True`, searches forward from `pos` (for overlap start).
        Otherwise, searches backwards from `pos` (for chunk end).

        Returns: the character position AFTER the boundary (so the boundary
        punctuation is included in the chunk).
        """
        if forward:
            # Search forward from pos for a sentence-ending punctuation
            search_text = text[pos:]
            match = re.search(r'[.!?]\s', search_text)
            if match:
                return pos + match.end()
            # Fallback: look for newline
            match = re.search(r'\n', search_text)
            if match:
                return pos + match.end()
            # Fallback: look for any whitespace (word boundary)
            match = re.search(r'\s', search_text)
            if match:
                return pos + match.end()
            return pos  # No boundary found

        # Search backwards from pos for a sentence boundary
        # Start with the overlap window, then extend to cover long words
        search_start = max(0, pos - self.overlap_chars)

        # First pass: look for sentence-ending punctuation (. ! ?) followed by space
        for i in range(min(pos, len(text) - 1), search_start, -1):
            if text[i] in ".!?":
                if i + 1 < len(text) and text[i + 1] in " \n\t\r":
                    return i + 1  # Include the punctuation, exclude the space
            if text[i] == "\n":
                return i + 1

        # Second pass: extend search to find ANY whitespace (word boundary)
        # This handles cases where a single word is longer than the overlap window
        for i in range(min(pos, len(text) - 1), 0, -1):
            if text[i] in " \t\n\r":
                return i + 1

        # Last resort: no boundary found — use pos (will split a word,
        # but only happens with extremely long words and no spaces)
        return pos

    def remap_offsets(self, entities: list[dict], chunk_start: int) -> list[dict]:
        """Add chunk_start to entity offsets so they're relative to full text.

        GLiNER returns offsets relative to the chunk (0 to chunk_length).
        We need offsets relative to the full document (0 to total_length).

        Args:
            entities: list of entity dicts with 'start' and 'end' keys.
            chunk_start: the offset of this chunk's start in the full text.

        Returns:
            The same list with adjusted offsets (modified in place).
        """
        for e in entities:
            e["start"] += chunk_start
            e["end"] += chunk_start
        return entities

    def merge_chunks(self, all_entities: list[list[dict]]) -> list[dict]:
        """Merge entities from all chunks, deduplicating overlap zone.

        Entities found in the overlap of chunk N and chunk N+1 will appear
        in both. This method deduplicates by (type, start, end) — if the
        same entity was found in both chunks, only one copy is kept.

        Also handles near-duplicates (same entity, slightly different
        span boundaries) by keeping the longer match.

        Args:
            all_entities: list where each element is the list of entities
                found in one chunk (with offsets already remapped to full text).

        Returns:
            Merged and deduplicated list, sorted by start offset.
        """
        # Flatten all entities
        flat: list[dict] = []
        for chunk_entities in all_entities:
            flat.extend(chunk_entities)

        if not flat:
            return []

        # Sort by start offset, then by length descending (longer first)
        flat.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))

        # Deduplicate: drop entities that overlap with an already-accepted one
        # of the same type. This handles the overlap zone where the same
        # entity appears in two chunks with slightly different boundaries.
        merged: list[dict] = []
        last_by_type: dict[str, tuple[int, int]] = {}

        for e in flat:
            e_type = e["type"]
            e_start = e["start"]
            e_end = e["end"]

            if e_type in last_by_type:
                last_start, last_end = last_by_type[e_type]
                # Check if this entity overlaps with the previous one of same type
                if e_start < last_end and last_start < e_end:
                    # Overlap — keep the longer one
                    if (e_end - e_start) > (last_end - last_start):
                        # Replace the last one with this (longer) one
                        merged = [m for m in merged if not (m["type"] == e_type and m["start"] == last_start)]
                        merged.append(e)
                        last_by_type[e_type] = (e_start, e_end)
                    # else: skip this one (shorter or same length)
                    continue

            # No overlap with previous — accept this entity
            merged.append(e)
            last_by_type[e_type] = (e_start, e_end)

        # Final sort by start offset
        merged.sort(key=lambda e: e["start"])
        return merged
