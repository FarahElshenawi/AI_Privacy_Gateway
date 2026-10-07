"""Export GLiNER2-PII model to ONNX format.

GLiNER2 doesn't have built-in ONNX export, but it's a PyTorch nn.Module.
This script exports the model using torch.onnx.export().

Usage:
    python -m dlp_core.tier2.export_onnx
    python -m dlp_core.tier2.export_onnx --model fastino/gliner2-privacy-filter-PII-multi --output models/gliner2_pii.onnx

After export, set Tier2Config(onnx_path="models/gliner2_pii.onnx", use_onnx=True)
in app/pipeline/engine.py to use ONNX inference (2-4x faster than PyTorch).

Requirements:
    pip install torch transformers gliner2 onnx onnxruntime

Note: The exported ONNX model includes the encoder + span decoder, but NOT
the text pre/post-processing (tokenization, label encoding, span decoding).
The ONNX runtime path in engine.py handles pre/post-processing in Python.
For a fully self-contained ONNX model, use optimum-exporters.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def export_to_onnx(model_name: str, output_path: str) -> None:
    """Export a GLiNER2 model to ONNX format.

    Args:
        model_name: HuggingFace model name (e.g., "fastino/gliner2-privacy-filter-PII-multi")
        output_path: Where to save the .onnx file
    """
    print(f"Loading model: {model_name}")
    from gliner2 import GLiNER2
    model = GLiNER2.from_pretrained(model_name)
    model.eval()  # inference mode

    print(f"Model loaded. Architecture: {type(model).__name__}")

    # Create a dummy input for tracing
    # GLiNER2's forward takes a PreprocessedBatch, but for ONNX export we
    # need to trace the underlying encoder.
    #
    # Strategy: export the encoder + decoder as a subgraph, keeping the
    # tokenizer/processor in Python (they're cheap).
    import torch

    # Get the model's config to understand input/output shapes
    config = model.config
    print(f"Model config: {config}")

    # Create dummy input matching the model's expected input shape
    # GLiNER2 uses BERT-like inputs: input_ids, attention_mask, token_type_ids
    max_seq_len = 384
    batch_size = 1

    dummy_input_ids = torch.ones(batch_size, max_seq_len, dtype=torch.long)
    dummy_attention_mask = torch.ones(batch_size, max_seq_len, dtype=torch.long)
    dummy_token_type_ids = torch.zeros(batch_size, max_seq_len, dtype=torch.long)

    # Ensure output directory exists
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Try to export the full model
    # If that fails, we export just the encoder part
    try:
        print(f"Attempting full model export to {output_path}...")

        # Wrap the model in a traceable format
        class GLiNER2Wrapper(torch.nn.Module):
            """Wrapper to make GLiNER2 traceable for ONNX export."""
            def __init__(self, model):
                super().__init__()
                self.model = model

            def forward(self, input_ids, attention_mask, token_type_ids):
                # Create a minimal batch dict that the model expects
                batch = {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                }
                output = self.model({"input_ids": input_ids, "attention_mask": attention_mask})
                return output

        wrapper = GLiNER2Wrapper(model)
        wrapper.eval()

        torch.onnx.export(
            wrapper,
            (dummy_input_ids, dummy_attention_mask, dummy_token_type_ids),
            str(output_path),
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "token_type_ids": {0: "batch", 1: "sequence"},
            },
            opset_version=17,
            do_constant_folding=True,
        )
        print(f"✅ ONNX model exported to {output_path}")
        print(f"   Size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")

    except Exception as e:
        print(f"❌ Full model export failed: {e}")
        print()
        print("Falling back to encoder-only export...")

        # Export just the encoder (BERT-like backbone)
        try:
            encoder = model.encoder if hasattr(model, "encoder") else model.backbone
            encoder.eval()

            torch.onnx.export(
                encoder,
                (dummy_input_ids, dummy_attention_mask),
                str(output_path),
                input_names=["input_ids", "attention_mask"],
                output_names=["last_hidden_state"],
                dynamic_axes={
                    "input_ids": {0: "batch", 1: "sequence"},
                    "attention_mask": {0: "batch", 1: "sequence"},
                },
                opset_version=17,
                do_constant_folding=True,
            )
            print(f"✅ Encoder exported to {output_path}")
            print(f"   Size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")
            print()
            print("⚠️  Note: Only the encoder was exported. The span decoder")
            print("   and label classifier remain in Python. This gives partial")
            print("   speedup (encoder is the bottleneck). Full ONNX export")
            print("   requires custom tracing of the decoder.")
        except Exception as e2:
            print(f"❌ Encoder export also failed: {e2}")
            print()
            print("The model may have dynamic control flow that prevents ONNX export.")
            print("Alternative: use `optimum-exporters` for HuggingFace-compatible export:")
            print(f"  pip install optimum-exporters")
            print(f"  optimum-export onnx --model {model_name} --task token-classification")
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export GLiNER2-PII to ONNX")
    parser.add_argument("--model", default="fastino/gliner2-privacy-filter-PII-multi",
                        help="HuggingFace model name")
    parser.add_argument("--output", default="models/gliner2_pii.onnx",
                        help="Output ONNX file path")
    args = parser.parse_args()

    export_to_onnx(args.model, args.output)
