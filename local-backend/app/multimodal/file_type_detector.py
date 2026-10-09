"""File type detector — decided by CONTENT (magic bytes / package layout), not by the name.

Detects: text, pdf, word (docx), excel (xlsx), image, unknown.

A file called report.docx that is really an xlsx is processed as Excel; a zip that is neither a
Word nor an Excel package, a legacy OLE .doc/.xls, or a file whose extension claims a binary
format its bytes don't have, is "unknown" (blocked) rather than guessed at.
"""
import zipfile
from pathlib import Path

_TEXT_EXTS = {".txt", ".md", ".csv", ".log", ".py", ".js", ".json", ".tsv"}
_BINARY_EXTS = {".pdf", ".docx", ".xlsx"}
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _zip_kind(file_path: str) -> str:
    try:
        with zipfile.ZipFile(file_path) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return "unknown"
    if "word/document.xml" in names:
        return "word"
    if "xl/workbook.xml" in names:
        return "excel"
    return "unknown"


def detect_file_type(file_path: str) -> str:
    """Returns: 'text', 'pdf', 'word', 'excel', 'image', 'unknown'."""
    ext = Path(file_path).suffix.lower()
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
    except OSError:
        return "unknown"

    if header.startswith(b"%PDF"):
        return "pdf"
    if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06"):
        return _zip_kind(file_path)
    if header.startswith(_OLE):
        return "unknown"
    if (header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8\xff")
            or header.startswith((b"GIF8", b"BM")) or (header.startswith(b"RIFF") and ext == ".webp")
            or header[:4] in (b"II*\x00", b"MM\x00*")):
        return "image"

    # Not a recognised binary container. A name that claims one is lying: don't guess.
    if ext in _BINARY_EXTS:
        return "unknown"

    # Text: UTF-8/UTF-16/32 (BOM) or legacy single-byte; reject anything with NUL bytes (binary).
    try:
        with open(file_path, "rb") as f:
            sample = f.read(4096)
    except OSError:
        return "unknown"
    if sample.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return "text"
    if b"\x00" in sample:
        return "unknown"
    if ext in _TEXT_EXTS:
        return "text"
    try:
        sample.decode("utf-8")
        return "text"
    except UnicodeDecodeError as exc:
        # a multibyte char cut at the 4 KB boundary is still text
        return "text" if exc.start >= len(sample) - 3 else "unknown"
