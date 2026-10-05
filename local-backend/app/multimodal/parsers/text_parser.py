"""Plain text parser — handles multiple encodings (UTF-8, UTF-16, Latin-1)."""
import re

# Common BOMs and their encodings
_BOM_MAP = [
    (b"\xef\xbb\xbf", "utf-8-sig"),       # UTF-8 with BOM
    (b"\xff\xfe\x00\x00", "utf-32-le"),   # UTF-32 LE
    (b"\x00\x00\xfe\xff", "utf-32-be"),   # UTF-32 BE
    (b"\xff\xfe", "utf-16-le"),           # UTF-16 LE
    (b"\xfe\xff", "utf-16-be"),           # UTF-16 BE
]


def _detect_encoding(raw: bytes) -> str:
    """Detect encoding from BOM. Falls back to UTF-8 if no BOM."""
    for bom, encoding in _BOM_MAP:
        if raw.startswith(bom):
            return encoding
    return "utf-8"


class TextParser:
    def parse(self, file_path: str) -> dict:
        """Extract text from a plain text file, handling multiple encodings.

        Reads the file in binary mode first, detects the encoding from
        the BOM (if present), then decodes to text. Falls back to UTF-8
        with errors='replace' if the detected encoding fails.

        Returns: {'text': str}
        """
        with open(file_path, "rb") as f:
            raw = f.read()

        encoding = _detect_encoding(raw)

        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            # Fallback: try UTF-8 with errors='replace'
            text = raw.decode("utf-8", errors="replace")

        return {"text": text}
