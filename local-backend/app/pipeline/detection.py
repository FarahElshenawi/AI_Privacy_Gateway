"""Step 1 — Detection orchestrator. Owner: Detection Engine.

Tiered hybrid: deterministic tier first (catches structured PII with ~100%
precision in ms), then semantic tier for the rest (names, orgs, locations).

The semantic tier uses the TextChunker to split long inputs into
overlapping chunks at sentence boundaries, so documents longer than
the model's context window (512 tokens) are handled correctly.

See docs/architecture.md §1 (Detection Engine) and the Tiered Detection Matrix.

Week 1 gate (docs/adr/0001-zero-shot-before-finetuning.md):
    Benchmark the semantic tier zero-shot against eval/eval_set_v1.json
    BEFORE committing to fine-tuning. Only proceed to fine-tuning if that
    gate fails.
"""
from __future__ import annotations

from typing import TypedDict

from app.pipeline.deterministic import detect as detect_deterministic, Entity
from app.pipeline.chunker import TextChunker


# === Lazy-loaded semantic tier (GLiNER2-PII via ONNX) ===

_semantic_model = None


def _get_semantic_model():
    """Lazy-load the GLiNER2-PII ONNX model. Returns None if unavailable."""
    global _semantic_model
    if _semantic_model is not None:
        return _semantic_model
    try:
        # TODO: implement — load ONNX model from local-backend/models/
        # import onnxruntime as ort
        # _semantic_model = ort.InferenceSession("models/gliner2_pii.onnx")
        _semantic_model = None
    except Exception as e:
        print(f"[detection] semantic tier unavailable: {e}")
        _semantic_model = None
    return _semantic_model


def _run_gliner_inference(model, text: str, schema: list[str]) -> list[Entity]:
    """Run GLiNER inference on a single chunk of text.

    This is the integration point for the actual GLiNER2-PII model.
    When the model is loaded, this function:
      1. Runs inference with the label schema
      2. Converts GLiNER output to Entity dict format
      3. Returns entities with chunk-local offsets (will be remapped by caller)

    TODO: implement actual ONNX inference when model is available.
    """
    # TODO: replace with actual GLiNER inference
    # result = model.predict_entities(text, schema, threshold=0.5)
    # return [Entity(type=e["label"], text=e["text"], start=e["start"], end=e["end"],
    #         confidence=e["score"], source="semantic") for e in result]
    return []


def _detect_semantic(text: str, schema: list[str]) -> list[Entity]:
    """Run the GLiNER2-PII semantic detector with chunking.

    Long text is split into overlapping chunks at sentence boundaries.
    Each chunk is processed independently, offsets are remapped to the
    full document, and results are merged (deduplicating the overlap zone).

    When the model is unavailable, returns empty list (graceful degradation).
    """
    model = _get_semantic_model()
    if model is None:
        return []

    chunker = TextChunker(max_tokens=400, overlap_tokens=50)
    chunks = chunker.chunk(text)

    all_chunk_entities = []
    for chunk_start, chunk_text in chunks:
        # Run GLiNER on this chunk
        chunk_entities = _run_gliner_inference(model, chunk_text, schema)

        # Remap offsets from chunk-local to full-document
        chunker.remap_offsets(chunk_entities, chunk_start)
        all_chunk_entities.append(chunk_entities)

    # Merge all chunks (deduplicates entities found in overlap zones)
    return chunker.merge_chunks(all_chunk_entities)


# === Public API ===

def detect(text: str, schema: list[str] | None = None) -> list[Entity]:
    """Run the tiered detector and return merged entities.

    Tier 1 (deterministic): always runs. Catches structured PII.
    Tier 2 (semantic): runs on text that may contain names/orgs/locations.
        Schema is the label set passed at inference (label-conditioned).
        Long text is automatically chunked at sentence boundaries with
        overlap, so documents of any length can be processed.

    Args:
        text: the prompt text to scan.
        schema: optional list of entity types for the semantic tier.
            Defaults to ["PERSON", "ORGANIZATION", "LOCATION"] if not
            provided.

    Returns:
        List of entities, sorted by start offset. Overlapping entities are
        resolved by preferring the longer match (so an AWS key starting with
        AKIA is kept over a coincidental credit-card substring inside it).
    """
    if schema is None:
        schema = ["PERSON", "ORGANIZATION", "LOCATION"]

    # === Tier 1: deterministic (always runs first, handles any length) ===
    deterministic_results = detect_deterministic(text)

    # === Tier 2: semantic (GLiNER2-PII, with chunking for long text) ===
    semantic_results = _detect_semantic(text, schema)

    # === Merge ===
    all_results = deterministic_results + semantic_results

    # Deduplicate — same span + type, keep first occurrence
    seen = set()
    deduped = []
    for e in all_results:
        key = (e["type"], e["start"], e["end"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    # Sort by start offset, then by length (longer first) for overlap resolution
    deduped.sort(key=lambda e: (e["start"], -(e["end"] - e["start"])))

    # Resolve overlaps: prefer longer match
    final = []
    last_end = -1
    for e in deduped:
        if e["start"] >= last_end:
            final.append(e)
            last_end = e["end"]
        # else: this entity overlaps with a previous (longer) one — drop it

    return final


# === CLI for smoke testing ===

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            sample = f.read()
    else:
        sample = """Hey team, my card is 4242 4242 4242 4242 and my email is john.doe@example.com.
        Server IP: 192.168.1.1. AWS key: AKIAIOSFODNN7EXAMPLE.
        Call me at +14155551234. I'm John Smith from Acme Corp."""

    results = detect(sample)
    for r in results:
        print(f"  [{r['type']:<14}] {r['text']!r:60} (src: {r['source']})")
    print(f"\n{len(results)} entities found.")
