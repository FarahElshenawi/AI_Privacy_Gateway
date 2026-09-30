"""File type detector — magic bytes + extension.

Detects: text, pdf, word (docx), excel (xlsx), image, unknown.
"""
from pathlib import Path


def detect_file_type(file_path: str) -> str:
    """Detect file type from magic bytes + extension.

    Returns: 'text', 'pdf', 'word', 'excel', 'image', 'unknown'
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    # Read magic bytes
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
    except OSError:
        return "unknown"

    # PDF magic: %PDF
    if header.startswith(b"%PDF"):
        return "pdf"

    # ZIP-based formats (docx, xlsx): PK\x03\x04
    if header.startswith(b"PK\x03\x04"):
        if ext == ".docx":
            return "word"
        if ext == ".xlsx":
            return "excel"
        # Could be other zip-based format — fall through to extension check

    # Image formats
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if header.startswith(b"\xff\xd8\xff"):
        return "image"  # JPEG
    if header.startswith(b"GIF8"):
        return "image"

    # Extension-based fallback
    text_exts = {".txt", ".md", ".csv", ".log", ".py", ".js", ".json", ".tsv"}
    if ext in text_exts:
        return "text"
    if ext == ".docx":
        return "word"
    if ext == ".xlsx":
        return "excel"
    if ext == ".pdf":
        return "pdf"

    # Last resort: try to decode as UTF-8 text
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            sample = f.read(1024)
        # Reject if the sample contains null bytes (binary file)
        if "\x00" in sample:
            return "unknown"
        return "text"
    except UnicodeDecodeError:
        pass

    return "unknown"
