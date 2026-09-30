"""PDF parser — extracts text per page using pdfplumber."""
import pdfplumber


class PDFParser:
    def parse(self, file_path: str) -> dict:
        """Extract text from a PDF, page by page.

        Returns: {'pages': [str, str, ...], 'page_count': int}
        """
        pages = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages.append(text)
        return {"pages": pages, "page_count": len(pages)}
