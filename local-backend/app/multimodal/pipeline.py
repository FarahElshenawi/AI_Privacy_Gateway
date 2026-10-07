"""Multimodal pipeline: extract segments -> detect -> mask by offset -> write edits in place -> verify.

Replaces the old pair-based flow. No (original, replacement) strings are ever built, so
nothing is applied by searching for text.

Fail-closed rules (success=False, no output file left behind):
  * the handler reports a blocker (scans, tracked deletions, comments, encryption, ...)
  * a critical detector failed
  * a handler cannot apply an edit, or the file changed between scan and write
  * the re-read output still contains hard-evidence PII (residual scan, plus a raw scan of
    every OOXML part for docx/xlsx)
  * `strict=True` and any detector tier was degraded (coverage incomplete)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Optional

from dlp_core.detection import DetectionResult
from dlp_core.masker import OffsetMasker
from dlp_core.segments import DetectionBlocked, SegmentMasker

from app.multimodal.file_type_detector import detect_file_type
from app.multimodal.handlers import get_handler, zip_raw_scan


class MultimodalPipeline:
    def __init__(self, detect: Optional[Callable[[str], DetectionResult]] = None,
                 masker: Optional[OffsetMasker] = None,
                 residual: Optional[Callable[[str], list]] = None, *,
                 strict: bool = False, max_batch_chars: int = 100_000) -> None:
        if detect is None or masker is None or residual is None:
            from app.pipeline.engine import _masker, detect as _detect, residual_scan
            detect, masker, residual = detect or _detect, masker or _masker, residual or residual_scan
        self._segmenter = SegmentMasker(detect, masker, max_batch_chars=max_batch_chars)
        self._residual = residual
        self._strict = strict

    def process(self, input_path: str, output_path: Optional[str] = None,
                conversation_id: str = "default") -> dict:
        src = Path(input_path)
        if not src.exists():
            return self._fail("error", "input_not_found")
        out = Path(output_path) if output_path else src.with_name(f"{src.stem}_masked{src.suffix}")

        ftype = detect_file_type(str(src))
        if ftype == "unknown":
            return self._fail(ftype, "unknown_file_type")
        if ftype == "image":
            return self._fail(ftype, "image_files_need_ocr_not_supported", blockers=["image_file"])

        handler = get_handler(ftype)
        try:
            extraction = handler.extract(str(src))
        except Exception as exc:  # noqa: BLE001 - never echo the message (may contain text)
            code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]{3,40}", str(exc)) else type(exc).__name__
            return self._fail(ftype, f"parse_failed:{code}")

        blockers = list(extraction.blockers)
        has_text = any(s.text.strip() for s in extraction.segments)
        if not has_text and extraction.uninspected and "no_extractable_text" not in blockers:
            blockers.append("no_extractable_text")
        if blockers:
            return self._fail(ftype, "blocked", blockers=sorted(set(blockers)), warnings=extraction.warnings)

        try:
            result = self._segmenter.mask_segments(extraction.segments, conversation_id)
        except DetectionBlocked:
            return self._fail(ftype, "detection_blocked_critical_detector_failed", warnings=extraction.warnings)
        except Exception as exc:  # noqa: BLE001
            return self._fail(ftype, f"masking_failed:{type(exc).__name__}", warnings=extraction.warnings)

        if self._strict and result.degraded:
            return self._fail(ftype, "degraded_coverage_strict_mode", warnings=extraction.warnings,
                              degraded=True, uncovered=result.uncovered_labels)

        try:
            post = handler.write(str(src), extraction, result, str(out))
        except Exception as exc:  # noqa: BLE001
            self._remove(out)
            return self._fail(ftype, f"write_failed:{type(exc).__name__}", warnings=extraction.warnings)
        if post:
            self._remove(out)
            return self._fail(ftype, "blocked", blockers=post, warnings=extraction.warnings)

        leaks = self._verify(handler, str(out), ftype)
        if leaks:
            self._remove(out)
        return {
            "success": not leaks, "input_type": ftype, "output_path": str(out) if not leaks else None,
            "replacements_made": result.spans_masked, "leaks": leaks, "blockers": [],
            "warnings": extraction.warnings, "degraded": result.degraded,
            "uncovered_labels": sorted(result.uncovered_labels), "error": None if not leaks else "residual_leak",
        }

    def _verify(self, handler, out: str, ftype: str) -> list[dict]:
        try:
            masked = handler.extract(out)
            text = "\n".join(s.text for s in masked.segments)
            leaks = list(self._residual(text))
            if ftype in ("word", "excel"):
                leaks += zip_raw_scan(out)
            return leaks
        except Exception as exc:  # noqa: BLE001
            return [{"type": "VERIFY_ERROR", "reason": f"could not re-read masked file ({type(exc).__name__})"}]

    @staticmethod
    def _remove(p: Path) -> None:
        try:
            os.unlink(p)
        except OSError:
            pass

    @staticmethod
    def _fail(ftype: str, error: str, *, blockers=None, warnings=None, degraded=False, uncovered=()) -> dict:
        return {"success": False, "input_type": ftype, "output_path": None, "replacements_made": 0,
                "leaks": [], "blockers": blockers or [], "warnings": warnings or [], "degraded": degraded,
                "uncovered_labels": sorted(uncovered), "error": error}
