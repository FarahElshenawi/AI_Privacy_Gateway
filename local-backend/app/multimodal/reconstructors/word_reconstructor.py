"""Word reconstructor — in-place replacement that survives split runs.

Word often splits one word across several runs (spell-check, formatting,
edits). We therefore match against the paragraph's joined text and map each
match back onto the runs it touches: the replacement goes into the first
touched run (keeping its formatting) and the matched characters are removed
from the others.
"""
import re

from docx import Document

from app.multimodal.docx_utils import iter_paragraphs, paragraph_runs


def _scrub_properties(doc) -> None:
    cp = doc.core_properties
    for attr in ("author", "last_modified_by", "title", "subject", "comments", "keywords"):
        try:
            setattr(cp, attr, "")
        except Exception:
            pass


class WordReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path (original .docx path)")

        doc = Document(original_path)
        mapping = {o: r for o, r in pairs if o}
        if mapping:
            pattern = re.compile("|".join(re.escape(o) for o in sorted(mapping, key=len, reverse=True)))
            for para in iter_paragraphs(doc):
                self._mask_paragraph(para, pattern, mapping)
        _scrub_properties(doc)
        doc.save(output_path)
        return output_path

    @staticmethod
    def _mask_paragraph(para, pattern, mapping) -> None:
        runs = paragraph_runs(para)
        if not runs:
            return
        texts = [r.text for r in runs]
        full = "".join(texts)
        matches = list(pattern.finditer(full))
        if not matches:
            return

        # char offset -> (run index)
        starts, pos = [], 0
        for t in texts:
            starts.append(pos)
            pos += len(t)

        def run_at(offset: int) -> int:
            for i in range(len(starts) - 1, -1, -1):
                if starts[i] <= offset:
                    return i
            return 0

        new_texts = list(texts)
        # Work right-to-left so earlier offsets stay valid
        for m in reversed(matches):
            first = run_at(m.start())
            last = run_at(m.end() - 1)
            repl = mapping[m.group(0)]
            for i in range(first, last + 1):
                lo = max(m.start(), starts[i]) - starts[i]
                hi = min(m.end(), starts[i] + len(texts[i])) - starts[i]
                seg = new_texts[i]
                new_texts[i] = seg[:lo] + (repl if i == first else "") + seg[hi:]

        for run, old, new in zip(runs, texts, new_texts):
            if old != new:
                run.text = new
