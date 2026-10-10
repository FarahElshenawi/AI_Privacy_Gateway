"""Handler registry: one handler per file type."""
from .base import Extraction, FileHandler, zip_raw_scan
from .excel import ExcelHandler
from .pdf import PdfHandler
from .text import TextHandler
from .word import WordHandler

HANDLERS = {"text": TextHandler, "pdf": PdfHandler, "word": WordHandler, "excel": ExcelHandler}


def get_handler(file_type: str) -> FileHandler:
    cls = HANDLERS.get(file_type)
    if cls is None:
        raise ValueError(f"no handler for file type: {file_type}")
    return cls()  # type: ignore[return-value]


__all__ = ["Extraction", "FileHandler", "HANDLERS", "get_handler", "zip_raw_scan"]
