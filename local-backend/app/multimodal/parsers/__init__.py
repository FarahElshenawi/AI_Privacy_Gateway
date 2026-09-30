"""Parser registry — one parser per file type."""
from .text_parser import TextParser
from .pdf_parser import PDFParser
from .word_parser import WordParser
from .excel_parser import ExcelParser


PARSERS = {
    "text": TextParser,
    "pdf": PDFParser,
    "word": WordParser,
    "excel": ExcelParser,
}


def get_parser(file_type: str):
    """Get the parser instance for a file type."""
    parser_class = PARSERS.get(file_type)
    if parser_class is None:
        raise ValueError(f"No parser for file type: {file_type}")
    return parser_class()
