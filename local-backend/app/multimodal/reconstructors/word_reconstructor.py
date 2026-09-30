"""Word document reconstructor — in-place text replacement.

Opens the ORIGINAL .docx and replaces PII text in every run, in place.
Preserves headers, footers, tables, images, styles, fonts, margins.
"""
from docx import Document


class WordReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        """Apply replacement pairs to the original Word document."""
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path")

        doc = Document(original_path)

        # Process body paragraphs
        for para in doc.paragraphs:
            self._mask_runs(para, pairs)

        # Process tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        self._mask_runs(para, pairs)

        # Process headers and footers
        for section in doc.sections:
            for para in section.header.paragraphs:
                self._mask_runs(para, pairs)
            for table in section.header.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            self._mask_runs(para, pairs)
            for para in section.footer.paragraphs:
                self._mask_runs(para, pairs)
            for table in section.footer.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            self._mask_runs(para, pairs)
            if section.first_page_header:
                for para in section.first_page_header.paragraphs:
                    self._mask_runs(para, pairs)
            if section.first_page_footer:
                for para in section.first_page_footer.paragraphs:
                    self._mask_runs(para, pairs)

        doc.save(output_path)
        return output_path

    def _mask_runs(self, para, pairs: list[tuple[str, str]]):
        """Apply replacement pairs to every run in a paragraph."""
        for run in para.runs:
            if run.text:
                for original, replacement in pairs:
                    if original in run.text:
                        run.text = run.text.replace(original, replacement)
