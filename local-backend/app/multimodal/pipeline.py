"""Multimodal pipeline: extract → detect → mask → reconstruct.

File-handling front end to the existing detection pipeline.
The detection engine and masker work on text — this layer handles files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.multimodal.file_type_detector import detect_file_type
from app.multimodal.parsers import get_parser
from app.multimodal.reconstructors import get_reconstructor
from app.pipeline.detection import detect
from app.pipeline.masking import mask_entities
from app.pipeline.residual_scanner import scan


class MultimodalPipeline:
    """Process a file: extract → detect → mask → reconstruct."""

    def process(
        self,
        input_path: str,
        output_path: str | None = None,
        conversation_id: str = "default",
    ) -> dict:
        """Process a file end-to-end.

        Args:
            input_path: Path to input file.
            output_path: Path for output file. If None, auto-generates.
            conversation_id: conversation ID for vault scoping.

        Returns:
            dict with: success, input_type, output_path, replacements_made, leaks.
        """
        input_path = Path(input_path)
        if not input_path.exists():
            return {"success": False, "error": f"Input file not found: {input_path}"}

        if output_path is None:
            output_path = input_path.with_name(f"{input_path.stem}_masked{input_path.suffix}")
        else:
            output_path = Path(output_path)

        # Step 1: Detect file type
        file_type = detect_file_type(str(input_path))
        if file_type == "unknown":
            return {"success": False, "error": f"Unknown file type: {input_path.name}"}
        if file_type == "image":
            return {"success": False, "error": "Image files require OCR (not supported in v1)"}

        # Step 2: Parse — extract text
        parser = get_parser(file_type)
        try:
            parsed_data = parser.parse(str(input_path))
        except (ValueError, Exception) as e:
            return {"success": False, "error": f"Failed to parse file: {e}"}
        parsed_data["metadata"] = {"path": str(input_path), "type": file_type}

        # Step 3: Detect — run detection engine on extracted text
        full_text = self._get_full_text(parsed_data, file_type)
        entities = detect(full_text)

        # Step 4: Mask — generate (original, replacement) pairs
        pairs = mask_entities(entities, conversation_id)

        # Step 5: Reconstruct — apply pairs to the original file
        reconstructor = get_reconstructor(file_type)
        reconstructor.reconstruct(parsed_data, pairs, str(output_path))

        # Step 6: Residual scan — re-check the masked file
        masked_parsed = parser.parse(str(output_path))
        masked_text = self._get_full_text(masked_parsed, file_type)
        leaks = scan(masked_text)

        return {
            "success": len(leaks) == 0,
            "input_type": file_type,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "replacements_made": len(pairs),
            "leaks": leaks,
        }

    def _get_full_text(self, parsed_data: dict, file_type: str) -> str:
        """Flatten parsed data into a single text string for detection."""
        if file_type == "text":
            return parsed_data.get("text", "")
        elif file_type == "pdf":
            return "\n".join(parsed_data.get("pages", []))
        elif file_type == "word":
            return "\n".join(p["text"] for p in parsed_data.get("paragraphs", []))
        elif file_type == "excel":
            parts = []
            for sheet in parsed_data.get("sheets", []):
                for cell in sheet.get("cells", []):
                    parts.append(cell["value"])
            return "\n".join(parts)
        return ""
