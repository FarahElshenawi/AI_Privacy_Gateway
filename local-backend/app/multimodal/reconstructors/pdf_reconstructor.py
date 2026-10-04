"""PDF reconstructor — in-place text replacement preserving original layout.

Uses PyMuPDF's redaction API with exact search-rectangle positioning:
  1. Search for all occurrences of the target text (single case-insensitive search)
  2. For each match, use the search rectangle's exact X/Y position
  3. Add a redaction annotation over ONLY the matched text
  4. Apply redactions (removes the original text but keeps everything else)
  5. Insert the masked text at the EXACT same position as the search rectangle

This preserves:
  - All images (vector and raster)
  - Page layout and structure
  - Non-PII text positioning
  - Annotations and form fields
  - Exact text position (no drift, no overlaps)
"""
import fitz  # PyMuPDF


class PDFReconstructor:
    """Reconstruct a PDF by replacing PII text in place."""

    def __init__(self):
        pass

    def reconstruct(self, parsed_data: dict, pairs: list[tuple[str, str]], output_path: str) -> str:
        """Reconstruct a PDF with PII text replaced in place.

        Args:
            parsed_data: must contain 'metadata.path' pointing to the
                original PDF file.
            output_path: where to write the masked PDF.

        Returns:
            Path to the output PDF.
        """
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path (original PDF path)")

        doc = fitz.open(original_path)

        for page_num in range(len(doc)):
            page = doc[page_num]
            self._mask_page(page, pairs)

        # Clear the author metadata — it is real-identity data, same class
        # of leak as PII in the page text (matches word_reconstructor).
        metadata = dict(doc.metadata or {})
        metadata["author"] = ""
        doc.set_metadata(metadata)

        doc.save(output_path, garbage=4, deflate=True, clean=True)
        doc.close()
        return output_path

    def _mask_page(self, page: fitz.Page, pairs: list[tuple[str, str]]) -> int:
        """Find and replace all PII occurrences on a single page.

        Uses search_for() to find exact rectangles, then:
        1. Gets font info from the span at that rectangle
        2. Adds redaction ONLY over the search rectangle (not the whole span)
        3. Inserts masked text at the rectangle's position
        """
        # Search for every (original, replacement) pair, longest originals
        # first so a full name is matched before its parts. Rects already
        # claimed by a longer match are skipped to avoid double replacement.
        text_dict = page.get_text("dict")
        replacements = []
        claimed: list[fitz.Rect] = []

        for original, replacement in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
            if not original or not original.strip():
                continue
            for rect in page.search_for(original):
                if any(rect.intersects(c) for c in claimed):
                    continue
                font_info = self._find_font_info(text_dict, rect)
                if font_info is None:
                    continue
                claimed.append(rect)
                replacements.append({
                    "rect": rect,
                    "masked_text": replacement,
                    "font_name": font_info["font"],
                    "font_size": font_info["size"],
                    "color": font_info["color"],
                    "origin_y": font_info["origin"][1],
                })

        # Apply ALL redactions first (batch)
        # This removes the original text but keeps everything else
        for r in replacements:
            page.add_redact_annot(r["rect"], fill=(1, 1, 1))

        page.apply_redactions()

        # Now insert the masked text at the EXACT position of each search rect
        # Use the rect's X0 (left edge) and the original span's baseline Y
        for r in replacements:
            self._insert_text_at_position(
                page,
                rect=r["rect"],
                text=r["masked_text"],
                font_name=r["font_name"],
                font_size=r["font_size"],
                color=r["color"],
                baseline_y=r["origin_y"],
            )

        return len(replacements)

    def _find_font_info(self, text_dict: dict, target_rect: fitz.Rect) -> dict | None:
        """Find the font info from the span that overlaps with the target rectangle.

        Returns: {font, size, color, origin}
        - origin is the (x, y) baseline of the span — we use the Y for insertion
        """
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_rect = fitz.Rect(span["bbox"])
                    if span_rect.intersects(target_rect):
                        return {
                            "font": span.get("font", "helv"),
                            "size": span.get("size", 11),
                            "color": self._int_to_rgb(span.get("color", 0)),
                            "origin": span.get("origin", (span["bbox"][0], span["bbox"][3] - 2)),
                        }
        return None

    def _int_to_rgb(self, color_int: int) -> tuple:
        """Convert PyMuPDF's integer color to (r, g, b) tuple in 0-1 range."""
        r = ((color_int >> 16) & 0xFF) / 255.0
        g = ((color_int >> 8) & 0xFF) / 255.0
        b = (color_int & 0xFF) / 255.0
        return (r, g, b)

    def _insert_text_at_position(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        text: str,
        font_name: str,
        font_size: float,
        color: tuple,
        baseline_y: float,
    ):
        """Insert text at the EXACT position of the search rectangle.

        Uses:
        - rect.x0 as the X position (left edge of where "Farah" started)
        - baseline_y as the Y position (baseline from the original span)

        This ensures the masked text appears exactly where the original was.
        """
        font_mapping = {
            "Helvetica": "helv",
            "Helvetica-Bold": "hebo",
            "Times-Roman": "tiro",
            "Times-Bold": "tibo",
            "Courier": "cour",
            "Courier-Bold": "cobo",
            "Arial": "helv",
            "Arial-Bold": "hebo",
        }
        pymupdf_font = font_mapping.get(font_name, "helv")

        # Use the search rect's X0 (exact left edge of "Farah")
        # and the original span's baseline Y
        insert_x = rect.x0
        insert_y = baseline_y

        try:
            page.insert_text(
                point=(insert_x, insert_y),
                text=text,
                fontname=pymupdf_font,
                fontsize=font_size,
                color=color,
                overlay=True,
            )
        except Exception:
            page.insert_text(
                point=(insert_x, insert_y),
                text=text,
                fontname="helv",
                fontsize=font_size,
                color=color,
                overlay=True,
            )
