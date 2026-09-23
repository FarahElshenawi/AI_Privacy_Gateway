"""Step 3 — SLM decision tier for ambiguous entities (~10%, e.g. ORGANIZATION).
Owner: Role 4.

Qwen-RLCD via ONNX. Week 1 Day 1 go/no-go gate: verify the ONNX export exists
before building on it (docs/development-plan.md, Week 1, Role 4, Mon).
Fallback order: DistilBERT -> type-tokens-only (drop the SLM tier).
"""

def decide(entity_text: str, entity_type: str, context: str) -> str:
    """Returns a masking action ('faker' | 'redact' | 'context' | 'keep')."""
    # TODO: ONNX inference, or fallback per Week 1 gate result
    raise NotImplementedError
