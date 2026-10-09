"""Word (.docx): paragraph-level masking mapped back onto formatting runs.

Covers body, nested tables, text boxes, headers/footers (all variants), hyperlink/field/
content-control runs, external hyperlink targets and core properties (author etc.).

Fails closed (blockers) on content we cannot mask safely: tracked deletions (the deleted text
is still in the file), comments, footnotes/endnotes with text, embedded objects, macros.
Images are not inspected: warned, and a document with images but no text is blocked.
"""
from __future__ import annotations

import re
import zipfile

from docx import Document

from app.multimodal.docx_utils import iter_paragraphs, paragraph_runs
from dlp_core.segments import Segment, SegmentMaskResult, apply_edits_to_runs

from .base import Extraction

_WT = re.compile(r"<w:t(?:\s[^>]*)?>([^<]+)</w:t>")
_CORE_FIELDS = ("author", "last_modified_by", "comments", "keywords", "subject", "category", "title",
                "content_status", "identifier", "language", "version")


def _inspect_package(path: str) -> tuple[list[str], list[str], bool]:
    blockers, warnings, media = [], [], False
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                low = name.lower()
                if low.startswith("word/media/"):
                    media = True
                elif low.startswith("word/embeddings/") or low.endswith("vbaproject.bin"):
                    blockers.append("embedded_objects_or_macros")
                elif low.startswith("word/") and low.endswith(".xml"):
                    xml = z.read(name).decode("utf-8", errors="ignore")
                    if "<w:delText" in xml:
                        blockers.append("tracked_deletions_present")
                    base = low.rsplit("/", 1)[-1]
                    if base.startswith(("comments", "footnotes", "endnotes")) and _WT.search(xml):
                        blockers.append(f"{base.split('.')[0].rstrip('0123456789')}_present")
    except zipfile.BadZipFile as exc:
        raise ValueError("not_a_valid_docx") from exc
    if media:
        warnings.append("images_not_inspected")
    return sorted(set(blockers)), warnings, media


def _table_headers(doc) -> dict:
    """paragraph element -> "Header: " for paragraphs in a table's body rows, when the first row has
    two or more non-empty cells. Read-only scan context so bare cell values hit cue-based patterns."""
    from docx.oxml.ns import qn
    out: dict = {}
    for tbl in doc.element.body.iter(qn("w:tbl")):          # document order: outer first, inner overrides
        rows = tbl.findall(qn("w:tr"))
        if len(rows) < 2:
            continue
        head = ["".join(t.text or "" for t in tc.iter(qn("w:t"))).strip()[:80]
                for tc in rows[0].findall(qn("w:tc"))]
        if sum(1 for h in head if h) < 2:
            continue
        for tr in rows[1:]:
            for ci, tc in enumerate(tr.findall(qn("w:tc"))):
                if ci < len(head) and head[ci]:
                    for p in tc.iter(qn("w:p")):
                        out[p] = f"{head[ci]}: "
    return out


class WordHandler:
    file_type = "word"

    def extract(self, path: str) -> Extraction:
        blockers, warnings, media = _inspect_package(path)
        try:
            doc = Document(path)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("cannot_open_docx") from exc
        ctx = _table_headers(doc)
        segs = [Segment(("p", i), "".join(r.text for r in paragraph_runs(p)), ctx.get(p._p, ""))
                for i, p in enumerate(iter_paragraphs(doc))]
        for rid, rel in doc.part.rels.items():
            if rel.is_external and rel.reltype.endswith("/hyperlink"):
                segs.append(Segment(("rel", rid), rel.target_ref))
        return Extraction(segs, blockers, warnings, uninspected=media)

    def write(self, src: str, extraction: Extraction, result: SegmentMaskResult, out: str) -> list[str]:
        doc = Document(src)
        original = {s.key: s.text for s in extraction.segments}
        for i, p in enumerate(iter_paragraphs(doc)):
            edits = result.edits.get(("p", i), ())
            if not edits:
                continue
            runs = paragraph_runs(p)
            run_texts = [r.text for r in runs]
            if "".join(run_texts) != original.get(("p", i)):
                raise RuntimeError("document_changed_between_scan_and_write")
            for run, old, new in zip(runs, run_texts, apply_edits_to_runs(run_texts, edits)):
                if new != old:                       # rewrite only touched runs (keeps drawings/fields in others)
                    run.text = new
        for rid, rel in doc.part.rels.items():
            if ("rel", rid) in result.edits and result.edits[("rel", rid)]:
                rel._target = result.masked[("rel", rid)]
        cp = doc.core_properties
        for attr in _CORE_FIELDS:
            try:
                setattr(cp, attr, "")
            except Exception:  # noqa: BLE001
                pass
        doc.save(out)
        return []
