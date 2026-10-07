"""Excel (.xlsx) via openpyxl: every string cell, formula text, comment, hyperlink, sheet
header/footer and workbook property; hidden sheets included.

Known limit (stated, not hidden): openpyxl drops charts, images, pivot tables and external
links when it saves. That removes their cached data too, so it never leaks, but the output is
a cleaned copy and `charts_images_pivots_dropped` is reported. Editing the OOXML directly
would preserve them and is the next improvement for this handler.
Sheet NAMES can't be changed without breaking formulas: sensitive data in a name blocks the file.
"""
from __future__ import annotations

import zipfile

from openpyxl import load_workbook
from openpyxl.comments import Comment

from dlp_core.segments import Segment, SegmentMaskResult

from .base import Extraction

_HF = ("oddHeader", "oddFooter", "evenHeader", "evenFooter", "firstHeader", "firstFooter")
_PROPS = ("creator", "lastModifiedBy", "title", "subject", "description", "keywords", "category")


def _package(path: str) -> tuple[list[str], list[str], bool]:
    blockers, warnings, media = [], [], False
    try:
        with zipfile.ZipFile(path) as z:
            names = [n.lower() for n in z.namelist()]
    except zipfile.BadZipFile as exc:
        raise ValueError("not_a_valid_xlsx") from exc
    if any(n.startswith("xl/embeddings/") or n.endswith("vbaproject.bin") for n in names):
        blockers.append("embedded_objects_or_macros")
    if any(n.startswith(("xl/media/",)) for n in names):
        media = True
        warnings.append("images_not_inspected")
    if any(n.startswith(("xl/charts/", "xl/drawings/", "xl/pivot", "xl/externallinks/")) for n in names):
        warnings.append("charts_images_pivots_dropped")
    return blockers, warnings, media


class ExcelHandler:
    file_type = "excel"

    def extract(self, path: str) -> Extraction:
        blockers, warnings, media = _package(path)
        try:
            wb = load_workbook(path, keep_links=False)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("cannot_open_xlsx") from exc
        segs: list[Segment] = []
        for si, ws in enumerate(wb.worksheets):
            segs.append(Segment(("title", si), ws.title))
            for cell in list(ws._cells.values()):
                v = cell.value
                if isinstance(v, str) and v:
                    segs.append(Segment(("c", si, cell.coordinate), v))
                if cell.comment is not None and cell.comment.text:
                    segs.append(Segment(("cm", si, cell.coordinate), cell.comment.text))
                hl = cell.hyperlink
                if hl is not None:
                    for attr in ("target", "location", "tooltip", "display"):
                        val = getattr(hl, attr, None)
                        if isinstance(val, str) and val:
                            segs.append(Segment(("hl", si, cell.coordinate, attr), val))
            for name in _HF:
                hf = getattr(ws, name, None)
                for part in ("left", "center", "right"):
                    txt = getattr(getattr(hf, part, None), "text", None) if hf is not None else None
                    if txt:
                        segs.append(Segment(("hf", si, name, part), txt))
        wb.close()
        return Extraction(segs, blockers, warnings, uninspected=media)

    def write(self, src: str, extraction: Extraction, result: SegmentMaskResult, out: str) -> list[str]:
        wb = load_workbook(src, keep_links=False)
        post: list[str] = []
        edited = {k for k, e in result.edits.items() if e}
        for si, ws in enumerate(wb.worksheets):
            if ("title", si) in edited:
                post.append("sheet_name_contains_sensitive_data")
            for cell in list(ws._cells.values()):
                k = ("c", si, cell.coordinate)
                if k in edited:
                    cell.value = result.masked[k]
                k = ("cm", si, cell.coordinate)
                if k in edited:
                    cell.comment = Comment(result.masked[k], "user")
                elif cell.comment is not None and cell.comment.author:
                    cell.comment = Comment(cell.comment.text, "user")        # author names are PII too
                if cell.hyperlink is not None:
                    for attr in ("target", "location", "tooltip", "display"):
                        k = ("hl", si, cell.coordinate, attr)
                        if k in edited:
                            setattr(cell.hyperlink, attr, result.masked[k])
            for name in _HF:
                hf = getattr(ws, name, None)
                for part in ("left", "center", "right"):
                    k = ("hf", si, name, part)
                    if k in edited:
                        setattr(getattr(hf, part), "text", result.masked[k])
        for attr in _PROPS:
            try:
                setattr(wb.properties, attr, None)
            except Exception:  # noqa: BLE001
                pass
        if not post:
            wb.save(out)
        wb.close()
        return sorted(set(post))
