"""Plain text: decode strictly, mask by offset, re-encode in the SAME encoding (BOM and line
endings are preserved because the text is never normalised)."""
from __future__ import annotations

from dlp_core.segments import Segment, SegmentMaskResult

from .base import Extraction

_BOMS = [(b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
         (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")]


def _decode(raw: bytes) -> tuple[str, str]:
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw.decode(enc), enc
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


class TextHandler:
    file_type = "text"

    def extract(self, path: str) -> Extraction:
        with open(path, "rb") as f:
            text, enc = _decode(f.read())
        return Extraction([Segment("text", text)], state=enc)

    def write(self, src: str, extraction: Extraction, result: SegmentMaskResult, out: str) -> list[str]:
        masked = result.masked["text"]
        enc = extraction.state
        data = masked.encode(enc, errors="replace")   # surrogates outside a legacy codepage become '?', never a crash
        with open(out, "wb") as f:
            f.write(data)
        return []
