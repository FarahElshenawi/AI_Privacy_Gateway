"""Step 1 — Detection orchestrator. Owner: Detection Engine.

Tiered hybrid: deterministic tier first (catches structured PII with ~100%
precision in ms), then semantic tier for the rest (names, orgs, locations).

See docs/architecture.md §1 (Detection Engine) and the Tiered Detection Matrix.

This module is the public API for the rest of the pipeline. Callers should
NOT import deterministic.py or semantic.py directly — go through detect()
here so the tiering logic stays in one place.

Week 1 gate (docs/adr/0001-zero-shot-before-finetuning.md):
    Benchmark the semantic tier zero-shot against eval/eval_set_v1.json
    BEFORE committing to fine-tuning. Only proceed to fine-tuning if that
    gate fails.
"""
from __future__ import annotations

from typing import TypedDict

from app.pipeline.deterministic import detect as detect_deterministic, Entity


# === Lazy-loaded semantic tier (GLiNER2-PII via ONNX) ===

_semantic_model = None


def _get_semantic_model():
    """Lazy-load the GLiNER2-PII ONNX model. Returns None if unavailable."""
    global _semantic_model
    if _semantic_model is not None:
        return _semantic_model
    try:
        # TODO: implement — load ONNX model from app/models/
        # For now, semantic tier is stubbed out and detect() falls back to
        # deterministic-only mode.
        #
        # import onnxruntime as ort
        # _semantic_model = ort.InferenceSession("app/models/gliner2_pii.onnx")
        _semantic_model = None
    except Exception as e:
        print(f"[detection] semantic tier unavailable: {e}")
        _semantic_model = None
    return _semantic_model


def _detect_semantic(text: str, schema: list[str]) -> list[Entity]:
    """Run the GLiNER2-PII semantic detector.

    Stubbed for now — returns empty list. Implementation comes in Step 1b
    after the deterministic tier is validated.

    When implementing:
        - Load ONNX model once (lazy)
        - Pass schema (label set) at inference, not training
        - Convert GLiNER output to Entity dict format
        - Skip spans that overlap with deterministic results
    """
    model = _get_semantic_model()
    if model is None:
        return []
    # TODO: ONNX inference here
    return []


# === Public API ===

def detect(text: str, schema: list[str] | None = None) -> list[Entity]:
    """Run the tiered detector and return merged entities.

    Tier 1 (deterministic): always runs. Catches structured PII.
    Tier 2 (semantic): runs on text that may contain names/orgs/locations.
        Schema is the label set passed at inference (label-conditioned).

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

    # === Tier 1: deterministic (always runs first) ===
    deterministic_results = detect_deterministic(text)

    # === Tier 2: semantic (GLiNER2-PII) ===
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
