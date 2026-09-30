"""PDF reconstructor — in-place replacement driven by (original, replacement) pairs.

For each pair, PyMuPDF finds the exact rectangles, a redaction removes the
original text (images and line-art are left alone), and the replacement is
drawn back at the same baseline, shrunk if needed to fit the original width.

Multi-line originals (e.g. PEM blocks) are searched line by line: the first
line gets the replacement, the rest are simply blanked.

Anything not found is NOT silently ignored: the pipeline re-parses the
output and the residual scanner fails the request if structured PII remains.
"""
try:
    import pymupdf as fitz  # PyMuPDF >= 1.24.3
except ImportError:  # pragma: no cover
    import fitz

from app.pipeline.masking import REDACTED  # noqa: F401  (re-exported for callers)

_FONT_MAP = {
    "helvetica": "helv", "arial": "helv",
    "helvetica-bold": "hebo", "arial-bold": "hebo", "arial,bold": "hebo",
    "times-roman": "tiro", "timesnewroman": "tiro",
    "times-bold": "tibo", "timesnewroman,bold": "tibo",
    "courier": "cour", "couriernew": "cour",
    "courier-bold": "cobo",
}


def _int_to_rgb(color_int: int) -> tuple:
    return (
        ((color_int >> 16) & 0xFF) / 255.0,
        ((color_int >> 8) & 0xFF) / 255.0,
        (color_int & 0xFF) / 255.0,
    )


class PDFReconstructor:
    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path (original PDF path)")

        # Longest first so a short original can't hit inside a longer one
        ordered = sorted((p for p in pairs if p[0]), key=lambda p: len(p[0]), reverse=True)

        doc = fitz.open(original_path)
        try:
            for page in doc:
                self._mask_page(page, ordered)
            doc.set_metadata({})          # author/title/etc. can contain names
            try:
                doc.del_xml_metadata()
            except Exception:
                pass
            doc.save(output_path, garbage=4, deflate=True, clean=True)
        finally:
            doc.close()
        return output_path

    def _mask_page(self, page: "fitz.Page", pairs: list[tuple[str, str]]) -> int:
        replacements = []
        for original, replacement in pairs:
            lines = [ln.strip() for ln in original.splitlines() if ln.strip()]
            for i, line in enumerate(lines):
                for rect in page.search_for(line):
                    style = self._style_at(page, rect)
                    replacements.append({
                        "rect": rect,
                        "text": replacement if i == 0 else "",
                        "style": style,
                    })
        if not replacements:
            return 0

        for r in replacements:
            page.add_redact_annot(r["rect"], fill=(1, 1, 1))

        # Remove only text: keep images and vector graphics intact
        kwargs = {}
        if hasattr(fitz, "PDF_REDACT_IMAGE_NONE"):
            kwargs["images"] = fitz.PDF_REDACT_IMAGE_NONE
        if hasattr(fitz, "PDF_REDACT_LINE_ART_NONE"):
            kwargs["graphics"] = fitz.PDF_REDACT_LINE_ART_NONE
        try:
            page.apply_redactions(**kwargs)
        except TypeError:
            page.apply_redactions()

        for r in replacements:
            if r["text"]:
                self._draw(page, r["rect"], r["text"], r["style"])
        return len(replacements)

    @staticmethod
    def _style_at(page: "fitz.Page", rect: "fitz.Rect") -> dict:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if fitz.Rect(span["bbox"]).intersects(rect):
                        return {
                            "font": span.get("font", "helv"),
                            "size": span.get("size", 11),
                            "color": _int_to_rgb(span.get("color", 0)),
                            "baseline": span.get("origin", (rect.x0, rect.y1 - 2))[1],
                        }
        return {"font": "helv", "size": 11, "color": (0, 0, 0), "baseline": rect.y1 - 2}

    @staticmethod
    def _draw(page: "fitz.Page", rect: "fitz.Rect", text: str, style: dict) -> None:
        font = _FONT_MAP.get(style["font"].split("+")[-1].lower().replace(" ", ""), "helv")
        size = style["size"]
        # Shrink (down to 60%) so a longer surrogate doesn't run over neighbours
        width = fitz.get_text_length(text, fontname=font, fontsize=size)
        if width > rect.width > 0:
            size = max(size * 0.6, size * rect.width / width)
        page.insert_text(
            (rect.x0, style["baseline"]), text,
            fontname=font, fontsize=size, color=style["color"], overlay=True,
        )
