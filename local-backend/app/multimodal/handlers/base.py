"""File handlers: one per format. Each knows how to EXTRACT segments and WRITE edits back.

Contract:
  extract(path) -> Extraction   segments to scan + blockers (reasons the file must NOT be
                                 sent) + warnings (sent, but the user should know)
  write(src, extraction, result, out) -> list[str]
                                 applies SegmentMaskResult edits in place; returns any
                                 post-write blockers (e.g. an edit that can't be applied)

Blockers and warnings are short machine codes, never document text.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Protocol

from dlp_core.residual_scanner import scan as residual_scan
from dlp_core.segments import Segment, SegmentMaskResult


@dataclass
class Extraction:
    segments: list[Segment]
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uninspected: bool = False      # content we can't read (images, scans): no text + this => block
    state: Any = None


class FileHandler(Protocol):
    file_type: str

    def extract(self, path: str) -> Extraction: ...

    def write(self, src: str, extraction: Extraction, result: SegmentMaskResult, out: str) -> list[str]: ...


_TAGS = re.compile(r"<[^>]+>")


def zip_raw_scan(path: str) -> list[dict]:
    """Safety net for OOXML (docx/xlsx): run the residual scanner over EVERY text part, including
    parts no handler parses (footnotes, chart caches, rels, custom XML). Only hard-evidence types
    (cards, keys, JWTs, IBANs, PEM) are checked, so Faker surrogates can't trip it."""
    findings: list[dict] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.lower().endswith((".xml", ".rels", ".txt", ".json", ".csv")):
                    continue
                if z.getinfo(name).file_size > 50_000_000:
                    findings.append({"type": "OVERSIZED_PART", "reason": "package part too large to scan"})
                    continue
                raw = z.read(name).decode("utf-8", errors="ignore")
                for f in residual_scan(_TAGS.sub(" ", raw)):
                    findings.append({"type": f["type"], "reason": f"{f['reason']} (in {name})"})
    except zipfile.BadZipFile:
        findings.append({"type": "BAD_PACKAGE", "reason": "output is not a valid OOXML package"})
    return findings
