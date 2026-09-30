"""Plain text reconstructor — preserves encoding and line endings.

Takes (original, replacement) pairs and applies them to the original
file in binary mode, preserving encoding, BOM, and line endings.
"""

_BOM_MAP = [
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
]


def _detect_encoding(raw: bytes) -> tuple[str, bytes | None]:
    for bom, encoding in _BOM_MAP:
        if raw.startswith(bom):
            return encoding, bom
    return "utf-8", None


def _detect_line_ending(text: str) -> str:
    crlf_count = text.count("\r\n")
    cr_count = text.count("\r") - crlf_count
    lf_count = text.count("\n") - crlf_count
    if crlf_count >= cr_count and crlf_count >= lf_count:
        return "\r\n"
    if cr_count > lf_count:
        return "\r"
    return "\n"


class TextReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        """Apply replacement pairs to the original text file."""
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path")

        with open(original_path, "rb") as f:
            raw = f.read()

        encoding, bom = _detect_encoding(raw)

        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            text = raw.decode("utf-8", errors="replace")
            encoding = "utf-8"
            bom = None

        if bom and text.startswith("\ufeff"):
            text = text[1:]

        line_ending = _detect_line_ending(text)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")

        # Apply each replacement pair
        for original, replacement in pairs:
            normalized = normalized.replace(original, replacement)

        if line_ending != "\n":
            normalized = normalized.replace("\n", line_ending)

        try:
            if encoding == "utf-8-sig":
                encoded = normalized.encode("utf-8-sig")
            else:
                encoded = normalized.encode(encoding)
                if bom:
                    encoded = bom + encoded
        except (UnicodeEncodeError, LookupError):
            encoded = normalized.encode("utf-8", errors="replace")

        with open(output_path, "wb") as f:
            f.write(encoded)

        return output_path
