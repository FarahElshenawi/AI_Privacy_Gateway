"""Plain text: decode strictly, mask by offset, re-encode in the SAME encoding (BOM and line
endings are preserved because the text is never normalised)."""
from __future__ import annotations

import os

from dlp_core.segments import Segment, SegmentMaskResult

from .base import Extraction

_MAX_TABLE_CHARS = 20_000_000
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


def _tokenize_table(text: str, delim: str) -> list[tuple[bool, str]]:
    """Split delimited text into (is_cell, raw) tokens. Quotes are honoured (a delimiter or newline
    inside "..." belongs to the cell); separators are kept verbatim so joining the tokens
    reproduces the input exactly."""
    toks: list[tuple[bool, str]] = []
    buf: list[str] = []
    inq = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            inq = not inq
            buf.append(ch)
        elif not inq and (ch == delim or ch in "\r\n"):
            toks.append((True, "".join(buf)))
            buf = []
            if ch == "\r" and text[i + 1:i + 2] == "\n":
                ch, i = "\r\n", i + 1
            toks.append((False, ch))
        else:
            buf.append(ch)
        i += 1
    toks.append((True, "".join(buf)))
    return toks


class TextHandler:
    file_type = "text"

    def extract(self, path: str) -> Extraction:
        with open(path, "rb") as f:
            text, enc = _decode(f.read())
        delim = {".csv": ",", ".tsv": "\t"}.get(os.path.splitext(path)[1].lower())
        if delim and len(text) <= _MAX_TABLE_CHARS:
            toks = _tokenize_table(text, delim)
            segs, col, header = [], 0, {}
            row_no = 0
            for ti, (is_cell, raw) in enumerate(toks):
                if not is_cell:
                    if raw in ("\n", "\r", "\r\n"):
                        col, row_no = 0, row_no + 1
                    else:
                        col += 1
                    continue
                if row_no == 0:
                    header[col] = raw.strip().strip('"').strip()[:80]
                ctx = f"{header[col]}: " if row_no > 0 and header.get(col) else ""
                segs.append(Segment(("t", ti), raw, ctx))
            return Extraction(segs, state=(enc, toks))
        return Extraction([Segment("text", text)], state=(enc, None))

    def write(self, src: str, extraction: Extraction, result: SegmentMaskResult, out: str) -> list[str]:
        enc, toks = extraction.state
        if toks is not None:
            masked = "".join(result.masked.get(("t", i), raw) if is_cell else raw
                             for i, (is_cell, raw) in enumerate(toks))
        else:
            masked = result.masked["text"]
        data = masked.encode(enc, errors="replace")   # surrogates outside a legacy codepage become '?', never a crash
        with open(out, "wb") as f:
            f.write(data)
        return []
