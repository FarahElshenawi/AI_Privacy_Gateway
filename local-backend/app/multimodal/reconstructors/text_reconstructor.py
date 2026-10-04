"""Plain text reconstructor — preserves encoding, BOM, and line endings.

Takes (original, replacement) pairs and applies them to the original
file in binary mode.
"""
from app.multimodal.parsers.text_parser import decode_text
from app.pipeline.masking import apply_pairs


class TextReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        """Apply replacement pairs to the original text file."""
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path")

        with open(original_path, "rb") as f:
            raw = f.read()

        # IMPORTANT: this must decode with the exact same logic
        # TextParser.parse() used for the detection step earlier in the
        # pipeline (pipeline.py calls parser.parse() to get the text that
        # `detect()` runs against). An earlier version of this method had
        # its own separate, weaker decode path here — BOM check, then
        # strict UTF-8, then UTF-8-with-errors="replace" as the final
        # fallback, with no cp1252/latin-1 attempt at all. For a
        # single-byte-encoded file (cp1252, latin-1 — common for
        # Windows-authored text with non-ASCII characters), that silently
        # mangled every non-ASCII byte into U+FFFD ("caf\xe9" -> "caf�")
        # BEFORE masking ever ran, independently of anything pairs-related.
        # Reusing decode_text() here guarantees the reconstructor sees
        # character-for-character the same string that detection did, so
        # pair positions/content line up and real bytes aren't lost.
        text, encoding = decode_text(raw)

        # Apply the pairs directly against `text`, with its ORIGINAL line
        # endings untouched. A multi-line entity like a PEM block has its
        # real \r\n baked into the pair's original string; normalizing
        # line endings before replacing (and converting back afterward,
        # as a still-earlier version of this method did) breaks that
        # exact-string match, so a multi-line secret could survive
        # "masking" completely unmasked.
        #
        # Longest-match-first, single pass — see apply_pairs' docstring
        # for why a naive unsorted loop can corrupt a PII value that is a
        # substring of another one.
        text, _ = apply_pairs(text, pairs)

        # Python's codecs add/strip the BOM themselves for utf-8-sig,
        # utf-16, and utf-32 on decode/encode — no manual BOM bookkeeping
        # needed as long as we encode with the same name decode_text()
        # returned.
        try:
            encoded = text.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            encoded = text.encode("utf-8", errors="replace")

        with open(output_path, "wb") as f:
            f.write(encoded)

        return output_path
