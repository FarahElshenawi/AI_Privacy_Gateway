"""Plain text reconstructor — preserves encoding, BOM and line endings.

Pairs are applied to the decoded text exactly as parsed (no line-ending
normalisation), so multi-line originals such as PEM blocks still match
and CRLF/LF/CR endings survive untouched.
"""
from app.multimodal.parsers.text_parser import decode_text
from app.pipeline.masking import apply_pairs


class TextReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path (original file path)")

        with open(original_path, "rb") as f:
            raw = f.read()

        text, encoding = decode_text(raw)
        masked, _ = apply_pairs(text, pairs)

        try:
            encoded = masked.encode(encoding)
        except UnicodeEncodeError:
            # A replacement isn't representable in the legacy encoding
            encoded = masked.encode("utf-8")

        with open(output_path, "wb") as f:
            f.write(encoded)
        return output_path
