"""Plain text parser — handles BOMs, UTF-8, and legacy single-byte encodings."""


def decode_text(raw: bytes) -> tuple[str, str]:
    """Decode raw bytes to text. Returns (text, encoding_used).

    The returned encoding can be passed back to str.encode() to write the
    file in its original encoding (BOM included where the codec adds one).
    Order: BOM -> strict UTF-8 -> cp1252 -> latin-1 (never fails).
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    # UTF-32 LE BOM starts with the UTF-16 LE BOM, so test it first
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        try:
            return raw.decode("utf-32"), "utf-32"
        except UnicodeDecodeError:
            pass
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


class TextParser:
    def parse(self, file_path: str) -> dict:
        """Returns: {'text': str, 'encoding': str}"""
        with open(file_path, "rb") as f:
            raw = f.read()
        text, encoding = decode_text(raw)
        return {"text": text, "encoding": encoding}
