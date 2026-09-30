"""Word document parser — extracts every paragraph (body, tables, headers, footers)."""
from docx import Document

from app.multimodal.docx_utils import iter_paragraphs, paragraph_runs


class WordParser:
    def parse(self, file_path: str) -> dict:
        """Returns: {'paragraphs': [{'text': str, 'runs': [(text, bold, italic), ...]}, ...]}

        Text is built from the same runs the reconstructor edits, so what
        detection sees is exactly what masking can change.
        """
        doc = Document(file_path)
        paragraphs = []
        for para in iter_paragraphs(doc):
            runs = paragraph_runs(para)
            text = "".join(r.text for r in runs)
            if not text:
                continue
            paragraphs.append({
                "text": text,
                "runs": [(r.text, r.bold, r.italic) for r in runs],
            })
        return {"paragraphs": paragraphs}
