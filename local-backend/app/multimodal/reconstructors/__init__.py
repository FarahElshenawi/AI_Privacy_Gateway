"""Reconstructor registry — one reconstructor per file type."""
from .text_reconstructor import TextReconstructor
from .pdf_reconstructor import PDFReconstructor
from .word_reconstructor import WordReconstructor
from .excel_reconstructor import ExcelReconstructor


RECONSTRUCTORS = {
    "text": TextReconstructor,
    "pdf": PDFReconstructor,
    "word": WordReconstructor,
    "excel": ExcelReconstructor,
}


def get_reconstructor(file_type: str):
    """Get the reconstructor instance for a file type."""
    reconstructor_class = RECONSTRUCTORS.get(file_type)
    if reconstructor_class is None:
        raise ValueError(f"No reconstructor for file type: {file_type}")
    return reconstructor_class()
