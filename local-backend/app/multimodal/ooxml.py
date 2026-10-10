"""OOXML package hygiene for .docx/.xlsx: parts the text handlers never look at.

Two jobs:
  * package_blockers(): content we cannot inspect or mask (charts, SmartArt, custom XML with text,
    glossary documents). Blocked by default; DLP_OOXML_PARTS=warn lets them through with a warning,
    and the raw-XML scan of the OUTPUT still blocks anything with hard-evidence PII in them.
  * scrub(): metadata that carries names/companies/images but is not document text, removed from the
    OUTPUT: custom properties, app properties (Company, Manager, titles), the first-page thumbnail,
    people.xml (reviewer names), tracked-change author names and core-property names.
"""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from typing import Iterable

_REL = re.compile(r"<Relationship\b[^>]*?/>", re.S)
_TARGET = re.compile(r'Target="([^"]*)"')
_TEXTISH = re.compile(r">([^<>\s][^<>]*)<")

_APP_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>')
_CUSTOM_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
               'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>')


def parts_policy() -> str:
    v = os.environ.get("DLP_OOXML_PARTS", "block").strip().lower()
    return v if v in ("block", "warn") else "block"


def package_blockers(path: str, kind: str) -> tuple[list[str], list[str]]:
    """(blockers, warnings) for parts we cannot inspect. kind: 'word' or 'excel'."""
    blockers: list[str] = []
    prefix = "word/" if kind == "word" else "xl/"
    try:
        with zipfile.ZipFile(path) as z:
            names = [n.lower() for n in z.namelist()]
            if kind == "word":                      # excel: openpyxl drops charts/drawings on save
                if any(n.startswith(prefix + "charts/") or n.startswith(prefix + "chartex/") for n in names):
                    blockers.append("charts_not_inspected")
                if any(n.startswith(prefix + "diagrams/") for n in names):
                    blockers.append("smartart_not_inspected")
                if any(n.startswith("word/glossary/") for n in names):
                    blockers.append("glossary_document_present")
            for n in z.namelist():
                if n.lower().startswith("customxml/item") and n.lower().endswith(".xml") \
                        and "props" not in n.lower() and _TEXTISH.search(z.read(n).decode("utf-8", "ignore")):
                    blockers.append("custom_xml_with_text")
                    break
    except zipfile.BadZipFile:
        return ["not_a_valid_package"], []
    if parts_policy() == "warn":
        warnings = [b for b in blockers if b in ("charts_not_inspected", "smartart_not_inspected")]
        return [b for b in blockers if b not in warnings], warnings
    return blockers, []


def _resolve(rels_name: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(posixpath.dirname(rels_name))      # part that owns the .rels file
    return posixpath.normpath(posixpath.join(base, target))


def _strip_rels(xml: str, rels_name: str, dropped: set[str]) -> str:
    def keep(m: re.Match) -> str:
        t = _TARGET.search(m.group(0))
        return "" if t and "TargetMode=\"External\"" not in m.group(0) and \
            _resolve(rels_name, t.group(1)).lower() in dropped else m.group(0)
    return _REL.sub(keep, xml)


def scrub(path: str) -> list[str]:
    """Rewrite the package at `path` without the metadata above. Returns what was removed."""
    done: list[str] = []
    with zipfile.ZipFile(path) as zin:
        infos = zin.infolist()
        low = {i.filename.lower(): i.filename for i in infos}
        dropped = {n for n in low if n.startswith("docprops/thumbnail.") or n == "word/people.xml"}
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=os.path.dirname(path) or None)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for info in infos:
                    name, ln = info.filename, info.filename.lower()
                    if ln in dropped:
                        done.append("thumbnail_removed" if "thumbnail" in ln else "reviewer_list_removed")
                        continue
                    data = zin.read(name)
                    if ln == "docprops/custom.xml":
                        data = _CUSTOM_XML.encode(); done.append("custom_properties_removed")
                    elif ln == "docprops/app.xml":
                        data = _APP_XML.encode(); done.append("app_properties_removed")
                    elif ln == "docprops/core.xml":
                        s = data.decode("utf-8", "ignore")
                        s = re.sub(r"<(dc:creator|cp:lastModifiedBy|cp:keywords|dc:description|dc:subject|dc:title)>.*?</\1>",
                                   r"<\1></\1>", s, flags=re.S)
                        data = s.encode()
                    elif ln.endswith(".rels") and dropped:
                        data = _strip_rels(data.decode("utf-8", "ignore"), name, dropped).encode()
                    elif ln == "[content_types].xml" and dropped:
                        s = data.decode("utf-8", "ignore")
                        for d in dropped:
                            s = re.sub(r'<Override\b[^>]*PartName="/%s"[^>]*/>' % re.escape(low[d]), "", s, flags=re.I)
                        data = s.encode()
                    elif re.fullmatch(r"word/[^/]+\.xml", ln):
                        s = data.decode("utf-8", "ignore")
                        s2 = re.sub(r'(\bw:author=")[^"]*(")', r"\1user\2", s)
                        s2 = re.sub(r'\sw:initials="[^"]*"', "", s2)
                        if s2 != s:
                            done.append("review_author_names_removed")
                        data = s2.encode()
                    zout.writestr(info, data)
            shutil.move(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return sorted(set(done))


def unique(items: Iterable[str]) -> list[str]:
    return sorted(set(items))
