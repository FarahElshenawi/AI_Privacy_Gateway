"""Helpers for walking every paragraph/run of a .docx, wherever it lives.

Covers the body, nested tables, text boxes, headers and footers (all
variants), and runs inside hyperlinks / tracked insertions.
"""
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

_RUN_XPATH = ("./w:r | ./w:hyperlink/w:r | ./w:ins/w:r | ./w:smartTag/w:r | ./w:fldSimple/w:r | "
              "./w:sdt/w:sdtContent/w:r | ./w:customXml/w:r | ./w:moveTo/w:r | ./w:hyperlink/w:fldSimple/w:r")


def _roots(doc):
    yield doc.element.body
    for section in doc.sections:
        for part in (
            section.header, section.footer,
            section.first_page_header, section.first_page_footer,
            section.even_page_header, section.even_page_footer,
        ):
            try:
                if part.is_linked_to_previous:      # no own definition: don't create one just by looking
                    continue
                yield part._element
            except Exception:
                continue


def iter_paragraphs(doc) -> list:
    """Return every Paragraph in the document as a list.

    Materialised up front on purpose: callers edit runs while looping, and
    mutating the tree during a lazy lxml iteration silently skips later
    nodes (this dropped table cells after an edited paragraph). Elements are
    also kept alive and deduped by element, not id(), which can be recycled.
    """
    found, seen = [], set()
    for root in _roots(doc):
        for p in list(root.iter(qn("w:p"))):
            if p in seen:
                continue
            seen.add(p)
            found.append(Paragraph(p, None))  # type: ignore[arg-type]  # no story part is intended
    return found


def paragraph_runs(paragraph) -> list:
    return [Run(r, paragraph) for r in paragraph._p.xpath(_RUN_XPATH)]


ALT_ATTRS = ("descr", "title", "name")


def iter_alt_elements(doc) -> list:
    """Every picture/shape/drawing property element (wp:docPr, pic:cNvPr, ...) that can carry alt text,
    a title or a file name, in document order. Materialised, de-duplicated by element."""
    found, seen = [], set()
    for root in _roots(doc):
        for el in list(root.iter()):
            tag = el.tag if isinstance(el.tag, str) else ""
            if tag.rsplit("}", 1)[-1] in ("docPr", "cNvPr") and el not in seen:
                seen.add(el)
                found.append(el)
    return found
