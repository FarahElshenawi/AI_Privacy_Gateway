"""Tests for the text chunker — sentence-boundary chunking with overlap.

Run:
    python -m pytest tests/test_chunker.py -v
"""
import pytest
from app.pipeline.chunker import TextChunker


class TestBasicChunking:
    def test_short_text_single_chunk(self):
        """Text shorter than max_chars returns one chunk starting at 0."""
        chunker = TextChunker(max_tokens=400, overlap_tokens=50)
        text = "Hello, my name is Farah. I work at Acme Corp."
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0][0] == 0  # offset 0
        assert chunks[0][1] == text  # full text

    def test_empty_text(self):
        chunker = TextChunker()
        assert chunker.chunk("") == [(0, "")]

    def test_exact_chunk_size(self):
        """Text exactly at max_chars returns one chunk."""
        chunker = TextChunker(max_tokens=100, overlap_tokens=20, chars_per_token=1)
        text = "A" * 100
        chunks = chunker.chunk(text)
        assert len(chunks) == 1

    def test_long_text_produces_multiple_chunks(self):
        """Text longer than max_chars is split into multiple chunks."""
        chunker = TextChunker(max_tokens=100, overlap_tokens=20, chars_per_token=1)
        # 500 chars, 5 sentences of 100 chars each
        text = ". ".join(["word " * 19 + "end"] * 5)  # ~500 chars
        chunks = chunker.chunk(text)
        assert len(chunks) > 1

    def test_chunks_cover_full_text(self):
        """The union of all chunks should cover the entire text."""
        chunker = TextChunker(max_tokens=100, overlap_tokens=20, chars_per_token=1)
        text = ". ".join(["This is a test sentence number " + str(i) for i in range(50)])
        chunks = chunker.chunk(text)

        # Last chunk should extend to the end of the text
        last_start, last_chunk = chunks[-1]
        assert last_start + len(last_chunk) >= len(text) - 1


class TestSentenceBoundaries:
    def test_chunks_end_at_sentence_boundary(self):
        """Each chunk should end at a sentence boundary (not mid-word)."""
        chunker = TextChunker(max_tokens=50, overlap_tokens=10, chars_per_token=1)
        # 200 chars with clear sentence endings
        sentences = [
            "This is sentence one. ",
            "This is sentence two. ",
            "This is sentence three. ",
            "This is sentence four. ",
            "This is sentence five. ",
        ]
        text = "".join(sentences)

        chunks = chunker.chunk(text)
        for start, chunk_text in chunks:
            # The chunk should end at a sentence boundary or end of text
            if chunk_text and not chunk_text.endswith(text[len(text)-1:]):
                # Not the last chunk — should end at . ! ? or newline
                assert chunk_text.rstrip()[-1] in ".!?\n", (
                    f"Chunk doesn't end at sentence boundary: ...{chunk_text[-20:]!r}"
                )

    def test_no_word_split(self):
        """No chunk should end in the middle of a word."""
        chunker = TextChunker(max_tokens=50, overlap_tokens=10, chars_per_token=1)
        text = "supercalifragilisticexpialidocious. " * 10
        chunks = chunker.chunk(text)

        for start, chunk_text in chunks:
            if chunk_text:
                # Should end with a space, punctuation, or newline — not a letter mid-word
                last_char = chunk_text.rstrip()[-1] if chunk_text.rstrip() else ""
                # Allow ending at the very end of full text
                if start + len(chunk_text) < len(text):
                    assert last_char in ".!?\n ", f"Chunk ends mid-word: {chunk_text[-15:]!r}"

    def test_newline_treated_as_boundary(self):
        """Double newline (paragraph break) should be a valid chunk boundary."""
        chunker = TextChunker(max_tokens=50, overlap_tokens=10, chars_per_token=1)
        text = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph here."
        chunks = chunker.chunk(text)

        # Should split at paragraph boundaries
        assert len(chunks) >= 1
        for start, chunk_text in chunks:
            # Verify the chunk text matches the original at that offset
            assert text[start:start + len(chunk_text)] == chunk_text


class TestOverlap:
    def test_chunks_overlap(self):
        """Adjacent chunks should overlap by ~overlap_chars."""
        chunker = TextChunker(max_tokens=100, overlap_tokens=20, chars_per_token=1)
        # Need text with sentences that are short enough to produce overlap
        text = ". ".join([f"Sentence {i}" for i in range(50)]) + "."
        chunks = chunker.chunk(text)

        if len(chunks) >= 2:
            # Check that chunk 2 starts before chunk 1 ends (overlap)
            start1, chunk1 = chunks[0]
            start2, chunk2 = chunks[1]
            # start2 should be less than (start1 + len(chunk1))
            assert start2 < start1 + len(chunk1), "No overlap between chunks"

    def test_entity_at_boundary_found_in_overlap(self):
        """An entity spanning a chunk boundary should appear in at least one chunk."""
        chunker = TextChunker(max_tokens=50, overlap_tokens=15, chars_per_token=1)

        # Place a name right at a likely boundary
        padding = "A. " * 15  # ~45 chars
        text = padding + "Contact Farah Ahmed today. " + "B. " * 15

        chunks = chunker.chunk(text)

        # "Farah Ahmed" should appear in at least one chunk
        found = False
        for start, chunk_text in chunks:
            if "Farah Ahmed" in chunk_text:
                found = True
                break

        assert found, "Entity at chunk boundary was not found in any chunk (overlap failed)"


class TestOffsetRemapping:
    def test_offsets_remap_correctly(self):
        """Entity offsets should be adjusted by chunk_start."""
        chunker = TextChunker()
        entities = [
            {"type": "PERSON", "text": "Farah", "start": 10, "end": 15, "confidence": 0.9},
        ]
        chunk_start = 500
        remapped = chunker.remap_offsets(entities, chunk_start)

        assert remapped[0]["start"] == 510
        assert remapped[0]["end"] == 515

    def test_offsets_remap_zero_start(self):
        """First chunk (offset 0) should have unchanged offsets."""
        chunker = TextChunker()
        entities = [
            {"type": "EMAIL", "text": "a@b.com", "start": 5, "end": 11},
        ]
        remapped = chunker.remap_offsets(entities, 0)
        assert remapped[0]["start"] == 5
        assert remapped[0]["end"] == 11


class TestMergeAndDedup:
    def test_merge_dedup_exact_duplicates(self):
        """Same entity found in two chunks (overlap zone) should be deduplicated."""
        chunker = TextChunker()
        chunk1_entities = [
            {"type": "PERSON", "text": "Farah", "start": 100, "end": 105},
        ]
        chunk2_entities = [
            {"type": "PERSON", "text": "Farah", "start": 100, "end": 105},
        ]
        merged = chunker.merge_chunks([chunk1_entities, chunk2_entities])
        assert len(merged) == 1
        assert merged[0]["start"] == 100

    def test_merge_keeps_longer_match(self):
        """When two overlapping entities of same type exist, keep the longer one."""
        chunker = TextChunker()
        entities = [
            {"type": "PERSON", "text": "Farah", "start": 100, "end": 105},
            {"type": "PERSON", "text": "Farah Ahmed", "start": 100, "end": 112},
        ]
        merged = chunker.merge_chunks([entities])
        assert len(merged) == 1
        assert merged[0]["text"] == "Farah Ahmed"

    def test_merge_preserves_different_types(self):
        """Different entity types at the same position are both kept."""
        chunker = TextChunker()
        entities = [
            {"type": "PERSON", "text": "Farah", "start": 100, "end": 105},
            {"type": "ORGANIZATION", "text": "Farah", "start": 100, "end": 105},
        ]
        merged = chunker.merge_chunks([entities])
        assert len(merged) == 2

    def test_merge_sorted_by_start(self):
        """Merged entities should be sorted by start offset."""
        chunker = TextChunker()
        entities = [
            {"type": "EMAIL", "text": "a@b.com", "start": 200, "end": 207},
            {"type": "PERSON", "text": "Farah", "start": 100, "end": 105},
            {"type": "PHONE", "text": "+1234", "start": 50, "end": 55},
        ]
        merged = chunker.merge_chunks([entities])
        assert merged[0]["start"] == 50
        assert merged[1]["start"] == 100
        assert merged[2]["start"] == 200


class TestChunkContent:
    def test_chunk_text_matches_original(self):
        """Each chunk's text should be a substring of the original at the correct offset."""
        chunker = TextChunker(max_tokens=100, overlap_tokens=20, chars_per_token=1)
        text = ". ".join([f"Sentence number {i} here" for i in range(30)]) + "."
        chunks = chunker.chunk(text)

        for start, chunk_text in chunks:
            assert text[start:start + len(chunk_text)] == chunk_text, (
                f"Chunk at offset {start} doesn't match original text"
            )

    def test_no_data_loss(self):
        """Every character in the original text should appear in at least one chunk."""
        chunker = TextChunker(max_tokens=100, overlap_tokens=20, chars_per_token=1)
        text = ". ".join([f"Sentence {i}." for i in range(100)])
        chunks = chunker.chunk(text)

        # Build coverage map
        covered = [False] * len(text)
        for start, chunk_text in chunks:
            for i in range(len(chunk_text)):
                if start + i < len(text):
                    covered[start + i] = True

        # Every character should be covered
        uncovered = [i for i, c in enumerate(covered) if not c]
        assert len(uncovered) == 0, f"{len(uncovered)} characters not covered by any chunk"
