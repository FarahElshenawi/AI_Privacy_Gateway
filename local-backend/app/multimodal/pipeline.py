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

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from dlp_core.detection import DetectionResult
from dlp_core.masker import OffsetMasker
from dlp_core.segments import DetectionBlocked, SegmentMasker

from app.multimodal import ooxml
from app.multimodal.file_type_detector import detect_file_type
from app.multimodal.handlers import get_handler, zip_raw_scan

# ── Timing diagnostics (metadata only, never document contents or PII) ──
_LOG = logging.getLogger("doppel.timing")


def _log_timing(stage: str, started: float, **metadata: object) -> None:
    """Log timing metadata only; never log document contents or PII."""
    elapsed_ms = (time.perf_counter() - started) * 1000
    details = " ".join(f"{k}={v}" for k, v in metadata.items())
    _LOG.info("[Doppel Timing] stage=%s elapsed_ms=%.1f %s", stage, elapsed_ms, details)


def _image_policy() -> str:
    from app.pipeline import engine
    return engine.IMAGE_POLICY


_EXT = {"word": ".docx", "excel": ".xlsx", "pdf": ".pdf"}


class MultimodalPipeline:
    def __init__(self, detect: Optional[Callable[[str], DetectionResult]] = None,
                 masker: Optional[OffsetMasker] = None,
                 residual: Optional[Callable[[str], list]] = None, *,
                 strict: bool = False, max_batch_chars: int = 100_000) -> None:
        if detect is None or masker is None or residual is None:
            from app.pipeline.engine import _masker, detect_file as _detect, residual_scan
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

        _t0 = time.perf_counter()
        ftype = detect_file_type(str(src))
        _log_timing("file.detect_type", _t0, file_type=ftype, input_size=src.stat().st_size)

        if ftype == "unknown":
            return self._fail(ftype, "unknown_file_type")
        policy = _image_policy()
        if ftype == "image":
            if policy == "warn":
                # Explicit org choice: the image goes out UNCHANGED (no OCR here), with a visible warning.
                shutil.copyfile(src, out)
                return {"success": True, "input_type": ftype, "output_path": str(out), "replacements_made": 0,
                        "leaks": [], "blockers": [], "warnings": ["image_not_inspected"], "degraded": False,
                        "uncovered_labels": [], "error": None}
            return self._fail(ftype, "image_files_need_ocr_not_supported", blockers=["image_file"])

        # The type comes from the content, but the libraries insist on the right extension: if the
        # name lies (an xlsx called report.docx), work on a correctly named copy.
        want = _EXT.get(ftype)
        if want and src.suffix.lower() != want:
            with tempfile.TemporaryDirectory() as td:
                res = self.process(str(shutil.copyfile(src, Path(td) / f"in{want}")),
                                   str(Path(td) / f"out{want}"), conversation_id)
                if res["success"]:
                    shutil.move(res["output_path"], out)
                    res["output_path"] = str(out)
                return res

        handler = get_handler(ftype)
        try:
            _t0 = time.perf_counter()
            extraction = handler.extract(str(src))
            _log_timing("file.extract", _t0, file_type=ftype, segments=len(extraction.segments), blockers=len(extraction.blockers))
        except Exception as exc:  # noqa: BLE001 - never echo the message (may contain text)
            code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]{3,40}", str(exc)) else type(exc).__name__
            return self._fail(ftype, f"parse_failed:{code}")

        blockers = list(extraction.blockers)
        if policy == "block" and "images_not_inspected" in extraction.warnings:
            blockers.append("images_present")
        # picture names / alt text are metadata, not body text: an image-only document has no text
        has_text = any(s.text.strip() for s in extraction.segments
                       if not (isinstance(s.key, tuple) and s.key and s.key[0] == "alt"))
        if not has_text and extraction.uninspected and "no_extractable_text" not in blockers:
            blockers.append("no_extractable_text")
        if blockers:
            return self._fail(ftype, "blocked", blockers=sorted(set(blockers)), warnings=extraction.warnings)

        try:
            _t0 = time.perf_counter()
            result = self._segmenter.mask_segments(extraction.segments, conversation_id)
            _log_timing("file.mask_segments", _t0, file_type=ftype, segments=len(extraction.segments), degraded=result.degraded, replacements=result.spans_masked)
        except DetectionBlocked:
            return self._fail(ftype, "detection_blocked_critical_detector_failed", warnings=extraction.warnings)
        except Exception as exc:  # noqa: BLE001
            return self._fail(ftype, f"masking_failed:{type(exc).__name__}", warnings=extraction.warnings)

        if self._strict and result.degraded:
            return self._fail(ftype, "degraded_coverage_strict_mode", warnings=extraction.warnings,
                              degraded=True, uncovered=result.uncovered_labels)

        try:
            _t0 = time.perf_counter()
            post = handler.write(str(src), extraction, result, str(out))
            _log_timing("file.write", _t0, file_type=ftype)
        except Exception as exc:  # noqa: BLE001
            self._remove(out)
            return self._fail(ftype, f"write_failed:{type(exc).__name__}", warnings=extraction.warnings)
        if post:
            self._remove(out)
            return self._fail(ftype, "blocked", blockers=post, warnings=extraction.warnings)
        if ftype in ("word", "excel"):
            try:
                _t0 = time.perf_counter()
                scrubbed = ooxml.scrub(str(out))
                _log_timing("file.ooxml_scrub", _t0, file_type=ftype, scrubbed=scrubbed)
            except Exception as exc:  # noqa: BLE001 - an unscrubbed package must not be sent
                self._remove(out)
                return self._fail(ftype, f"scrub_failed:{type(exc).__name__}", warnings=extraction.warnings)
            if scrubbed:
                extraction.warnings.append("metadata_scrubbed")

        _t0 = time.perf_counter()
        leaks = self._verify(handler, str(out), ftype)
        _log_timing("file.verify", _t0, file_type=ftype, leak_count=len(leaks))
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
