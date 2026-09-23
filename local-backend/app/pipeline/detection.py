"""Step 1 — Detection. Owner: Role 1.

GLiNER2-PII via ONNX, label-conditioned: the target entity schema is passed at
inference, not baked in by retraining (docs/architecture.md 1.2).

Week 1 gate (docs/adr/0001-zero-shot-before-finetuning.md): benchmark this
module zero-shot against eval/eval_set_v1.json BEFORE committing to the
fine-tuning track. Only proceed to fine-tuning if that gate fails.
"""

def detect(text: str, schema: list[str]) -> list[dict]:
    """Returns character-span entities: [{type, text, start, end, confidence}, ...]"""
    # TODO: load ONNX model, run inference with schema as label set
    raise NotImplementedError
