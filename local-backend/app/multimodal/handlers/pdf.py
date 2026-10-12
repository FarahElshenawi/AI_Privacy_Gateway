"""PDF via PyMuPDF: CHARACTER-level masking.

Each page's text is rebuilt from per-character boxes, so detection offsets map exactly to
glyph rectangles. Only the characters inside a detected span are redacted (the underlying text
is removed, not just covered) and the replacement is drawn at that spot. A value like
"123" in "12345", or "John" in "Johnson", is never touched.

Fails closed on what can't be inspected: no extractable text at all, scanned/image-only pages,
encryption, embedded files, annotations or form fields that carry text. Images are not OCR'd:
warned. Metadata (Info + XMP), link targets and bookmarks are scanned/scrubbed.
"""
from __future__ import annotations

import unicodedata

import pymupdf

from dlp_core.segments import Segment, SegmentMaskResult

from .base import Extraction

_MIN_PAGE_TEXT_WITH_IMAGES = 20


def _page_model(page: "pymupdf.Page"):
    """Returns (text, boxes) where boxes[i] describes text[i] or None for synthetic newlines."""
    raw = page.get_text("rawdict")
    text: list[str] = []
    boxes: list = []
    line_no = 0
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_no += 1
            for sp in line.get("spans", []):
                info = (sp.get("font", ""), float(sp.get("size", 11)), int(sp.get("color", 0)))
                for ch in sp.get("chars", []):
                    c = ch.get("c", "")
                    for _ in c:
                        text.append(_)
                        boxes.append((pymupdf.Rect(ch["bbox"]), info, tuple(ch.get("origin", (0, 0))), line_no))
            text.append("\n")
            boxes.append(None)
    return "".join(text), boxes


def _font_for(name: str) -> str:
    n = name.lower()
    bold = "bold" in n or "black" in n or "heavy" in n
    if "courier" in n or "mono" in n or "consol" in n:
        return "cobo" if bold else "cour"
    if "times" in n or "serif" in n or "georgia" in n or "garamond" in n:
        return "tibo" if bold else "tiro"
    return "hebo" if bold else "helv"


def _latin(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("latin-1", "ignore").decode("latin-1")


class PdfHandler:
    file_type = "pdf"

    def extract(self, path: str) -> Extraction:
        try:
            doc = pymupdf.open(path)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("cannot_open_pdf") from exc
        blockers: list[str] = []
        warnings: list[str] = []
        segs: list[Segment] = []
        uninspected = False
        try:
            if doc.needs_pass or doc.is_encrypted:
                return Extraction([], ["encrypted_pdf"], [], True)
            if doc.embfile_count() > 0:
                blockers.append("embedded_files_present")
            total = 0
            for i, page in enumerate(doc.pages()):
                text, _ = _page_model(page)
                total += len(text.strip())
                has_images = bool(page.get_images(full=True))
                if has_images:
                    uninspected = True
                    if len(text.strip()) < _MIN_PAGE_TEXT_WITH_IMAGES:
                        blockers.append("scanned_or_image_only_page")
                segs.append(Segment(("page", i), text))
                for j, link in enumerate(page.get_links()):
                    uri = link.get("uri")
                    if uri:
                        segs.append(Segment(("link", i, j), uri))
                for annot in page.annots() or []:
                    info = annot.info or {}
                    if (info.get("content") or "").strip() or (info.get("title") or "").strip():
                        blockers.append("annotations_with_text")
                        break
                for w in page.widgets() or []:
                    if str(w.field_value or "").strip():
                        blockers.append("form_fields_with_values")
                        break
            if total == 0:
                blockers.append("no_extractable_text")
            if uninspected:
                warnings.append("images_not_inspected")
            for j, (_, title, _) in enumerate(doc.get_toc(simple=True)):
                segs.append(Segment(("toc", j), title))
        finally:
            doc.close()
        return Extraction(segs, sorted(set(blockers)), warnings, uninspected)

    def write(self, src: str, extraction: Extraction, result: SegmentMaskResult, out: str) -> list[str]:
        doc = pymupdf.open(src)
        original = {s.key: s.text for s in extraction.segments}
        try:
            for i, page in enumerate(doc.pages()):
                for j, link in enumerate(page.get_links()):
                    if result.edits.get(("link", i, j)):
                        page.delete_link(link)           # a link target that holds sensitive data is removed
                edits = result.edits.get(("page", i), ())
                if edits:
                    self._mask_page(page, original[("page", i)], edits)
            toc = doc.get_toc(simple=True)
            if any(result.edits.get(("toc", j)) for j in range(len(toc))):
                doc.set_toc([[lvl, result.masked.get(("toc", j), title), pg] for j, (lvl, title, pg) in enumerate(toc)])
            doc.set_metadata({})
            try:
                doc.del_xml_metadata()
            except Exception:  # noqa: BLE001
                pass
            doc.save(out, garbage=4, deflate=True, clean=True)
        finally:
            doc.close()
        return []

    @staticmethod
    def _mask_page(page: "pymupdf.Page", expected_text: str, edits) -> None:
        text, boxes = _page_model(page)
        if text != expected_text:
            raise RuntimeError("page_changed_between_scan_and_write")
        plan = []
        for e in edits:
            by_line: dict[int, list] = {}
            for idx in range(e.start, e.end):
                b = boxes[idx]
                if b is not None:
                    by_line.setdefault(b[3], []).append(b)
            if not by_line:
                continue
            first = by_line[min(by_line)]
            rects = [pymupdf.Rect(min(c[0].x0 for c in cs), min(c[0].y0 for c in cs),
                                  max(c[0].x1 for c in cs), max(c[0].y1 for c in cs)) for cs in by_line.values()]
            plan.append((rects, first[0], e.replacement))
        for rects, *_ in plan:
            for r in rects:        # inset: touching neighbours must not be swept into the redaction
                page.add_redact_annot(pymupdf.Rect(r.x0 + 0.2, r.y0 + 0.4, r.x1 - 0.2, r.y1 - 0.4), fill=(1, 1, 1))
        try:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)  # type: ignore[attr-defined]
        except TypeError:
            page.apply_redactions()
        for rects, head, repl in plan:
            (font, size, color), origin = head[1], head[2]
            fontname = _font_for(font)
            rgb = (((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255)
            s = _latin(repl)
            width = rects[0].width
            need = pymupdf.get_text_length(s, fontname=fontname, fontsize=size)
            if need > width > 0:
                size = max(5.0, size * width / need)
            page.insert_text((rects[0].x0, origin[1]), s, fontname=fontname, fontsize=size, color=rgb, overlay=True)
