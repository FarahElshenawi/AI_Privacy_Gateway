"""Word document reconstructor — in-place text replacement.

Opens the ORIGINAL .docx and replaces PII text in every run, in place.
Preserves headers, footers, tables, images, styles, fonts, margins.
"""
from docx import Document

from app.pipeline.masking import apply_pairs


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

        # The author is real-identity metadata, same class of leak as PII
        # in the body text — clear it rather than leaving the real
        # document author attached to a "masked" file.
        doc.core_properties.author = ""

        doc.save(output_path)
        return output_path

    def _mask_runs(self, para, pairs: list[tuple[str, str]]):
        """Apply replacement pairs across a whole paragraph, not run by run.

        Word frequently splits one piece of visible text across several
        runs for reasons that have nothing to do with content — spell-
        check markers, a mid-word formatting change, autocomplete, etc.
        An entity like "john.doe@example.com" can easily end up as
        "john.do" in one run and "e@example.com" in the next. Replacing
        pair-by-pair WITHIN each run (as an earlier version of this
        method did) never sees that — neither run's text alone contains
        the full email — so the PII silently survives unmasked.

        Fix: join the whole paragraph's text first, run the replacement
        against that, and only then write it back. If anything changed,
        the masked text goes entirely into the first run (keeping that
        run's formatting) and every other run in the paragraph is
        emptied — once a PII span has been found to cross a run
        boundary, there's no reliable way to know which masked character
        belongs to which original run's formatting, so this trades a
        little intra-paragraph formatting fidelity for the thing that
        actually matters here: the PII is gone, not partially there.
        """
        if not para.runs:
            return
        full_text = "".join(run.text for run in para.runs)
        if not full_text:
            return
        masked_text, count = apply_pairs(full_text, pairs)
        if count == 0:
            return
        para.runs[0].text = masked_text
        for run in para.runs[1:]:
            run.text = ""
