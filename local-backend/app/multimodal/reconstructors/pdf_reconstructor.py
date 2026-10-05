"""PDF reconstructor — in-place text replacement preserving original layout.

Uses PyMuPDF's redaction API with exact search-rectangle positioning:
  1. For each (original, replacement) pair from the masking pipeline:
     a. Search for all occurrences of the ORIGINAL text on each page
     b. For each match, capture font info + position
  2. Apply all redactions (removes original text, keeps layout/images)
  3. Insert masked text at the EXACT same positions

This preserves:
  - All images (vector and raster)
  - Page layout and structure
  - Non-PII text positioning
  - Annotations and form fields
  - Exact text position (no drift, no overlaps)
"""
import fitz  # PyMuPDF


class PDFReconstructor:
    """Reconstruct a PDF by replacing PII text in place using masking pairs."""

    def __init__(self):
        # No hardcoded patterns — pairs come from the masking pipeline
        pass

    def reconstruct(self, parsed_data: dict, pairs: list, output_path: str) -> str:
        """Reconstruct a PDF with PII text replaced in place.

        Args:
            parsed_data: must contain 'metadata.path' pointing to the
                original PDF file.
            pairs: list of (original_text, replacement_text) tuples from
                the masking pipeline.
            output_path: where to write the masked PDF.

        Returns:
            Path to the output PDF.
        """
        original_path = parsed_data.get("metadata", {}).get("path")
        if original_path is None:
            raise ValueError("parsed_data must contain metadata.path (original PDF path)")

        pairs = pairs or []

        doc = fitz.open(original_path)

        # Sort pairs by original length (longest first) so that longer
        # matches (e.g. full names) are processed before shorter substrings
        # (e.g. first names). This prevents double-replacement.
        sorted_pairs = sorted(pairs, key=lambda p: len(p[0]), reverse=True)

        total_replacements = 0
        for page_num in range(len(doc)):
            page = doc[page_num]
            total_replacements += self._mask_page(page, sorted_pairs)

        doc.save(output_path, garbage=4, deflate=True, clean=True)
        doc.close()
        return output_path

    def _mask_page(self, page: fitz.Page, pairs: list) -> int:
        """Find and replace all PII occurrences on a single page.

        For each (original, replacement) pair:
          1. search_for(original) → list of rects
          2. For each rect, capture font info + position
          3. Add redaction annotation
        Then apply all redactions in batch, then insert masked text.
        """
        if not pairs:
            return 0

        text_dict = page.get_text("dict")
        replacements = []
        claimed_rects = []  # track rects already claimed by a longer match

        for original, replacement in pairs:
            if not original or not replacement:
                continue
            if original == replacement:
                continue

            # search_for is case-insensitive by default
            search_results = page.search_for(original)

            for rect in search_results:
                # Skip if this rect was already claimed by a longer match
                if any(self._rects_overlap(rect, c) for c in claimed_rects):
                    continue

                # Find font info from the span that overlaps this rect
                font_info = self._find_font_info(text_dict, rect)
                if font_info is None:
                    continue

                # Get the actual text at this rectangle (preserves case)
                original_text = self._get_text_in_rect(page, rect)
                if not original_text:
                    continue

                # Compute masked text with case preservation
                masked_text = self._preserve_case(original_text, original, replacement)
                if masked_text == original_text:
                    continue

                replacements.append({
                    "rect": rect,
                    "masked_text": masked_text,
                    "font_name": font_info["font"],
                    "font_size": font_info["size"],
                    "color": font_info["color"],
                    "origin_y": font_info["origin"][1],
                })
                claimed_rects.append(rect)

        # Apply ALL redactions first (batch — removes original text)
        for r in replacements:
            page.add_redact_annot(r["rect"], fill=(1, 1, 1))
        page.apply_redactions()

        # Insert masked text at the EXACT position of each search rect
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

    def _rects_overlap(self, r1: fitz.Rect, r2: fitz.Rect) -> bool:
        """Check if two rectangles overlap (used to avoid double-replacement)."""
        return r1.intersects(r2)

    def _find_font_info(self, text_dict: dict, target_rect: fitz.Rect) -> dict | None:
        """Find the font info from the span that overlaps with the target rectangle.

        Returns: {font, size, color, origin}
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

    def _get_text_in_rect(self, page: fitz.Page, rect: fitz.Rect) -> str:
        """Extract the actual text at the given rectangle position."""
        text = page.get_textbox(rect)
        text = " ".join(text.split())
        return text.strip()

    def _int_to_rgb(self, color_int: int) -> tuple:
        """Convert PyMuPDF's integer color to (r, g, b) tuple in 0-1 range."""
        r = ((color_int >> 16) & 0xFF) / 255.0
        g = ((color_int >> 8) & 0xFF) / 255.0
        b = (color_int & 0xFF) / 255.0
        return (r, g, b)

    def _preserve_case(self, actual_text: str, search_text: str, replacement: str) -> str:
        """Replace search_text with replacement in actual_text, preserving case.

        If the original was all-caps, the replacement is uppercased.
        If the original was capitalized (First letter), so is the replacement.
        Otherwise, lowercase.
        """
        # Find the actual case variant in actual_text
        import re
        # Case-insensitive search for the actual occurrence
        pattern = re.compile(re.escape(search_text), re.IGNORECASE)

        def replacer(match):
            matched = match.group(0)
            if matched.isupper():
                return replacement.upper()
            elif matched[0].isupper():
                return replacement[0].upper() + replacement[1:]
            else:
                return replacement.lower()

        return pattern.sub(replacer, actual_text)

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
        """Insert text at the EXACT position of the search rectangle."""
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
