"""Regression tests for the file-masking flow (text / docx / xlsx / pdf)."""
import io

import docx
import openpyxl
import pymupdf
import pytest

from app.multimodal.pipeline import MultimodalPipeline
from app.pipeline.masking import apply_pairs, mask_entities

EMAIL = "john.doe@example.com"


def run(tmp_path, name, data, cid):
    src = tmp_path / name
    src.write_bytes(data)
    out = tmp_path / ("masked_" + name)
    return MultimodalPipeline().process(str(src), str(out), cid), out


def test_apply_pairs_single_pass_longest_first():
    text, n = apply_pairs("a@x.com and a@x.com.au", [("a@x.com", "B"), ("a@x.com.au", "C")])
    assert (text, n) == ("B and C", 2)


def test_same_real_value_gets_same_fake_within_conversation():
    ents = [{"type": "EMAIL", "text": EMAIL}]
    first = mask_entities(ents, "consistency")
    second = mask_entities(ents, "consistency")
    assert first == second


def test_text_keeps_crlf_pem_and_legacy_encoding(tmp_path):
    pem = "-----BEGIN RSA PRIVATE KEY-----\r\nMIIEabc123\r\n-----END RSA PRIVATE KEY-----"
    raw = f"mail {EMAIL}\r\ncaf\xe9\r\n{pem}\r\n".encode("cp1252")
    res, out = run(tmp_path, "a.txt", raw, "t-text")
    assert res["success"]
    data = out.read_bytes()
    assert EMAIL.encode() not in data and b"PRIVATE KEY" not in data
    assert b"caf\xe9\r\n" in data


def test_docx_masks_split_runs_tables_headers_and_author(tmp_path):
    d = docx.Document()
    p = d.add_paragraph()
    p.add_run("Contact john.do")
    p.add_run("e@example.com now")
    d.add_table(rows=1, cols=1).cell(0, 0).text = f"Table {EMAIL}"
    d.sections[0].header.paragraphs[0].text = f"Hdr {EMAIL}"
    d.core_properties.author = "Real Author"
    buf = io.BytesIO()
    d.save(buf)
    res, out = run(tmp_path, "a.docx", buf.getvalue(), "t-docx")
    assert res["success"]
    o = docx.Document(str(out))
    texts = [x.text for x in o.paragraphs] + [o.tables[0].cell(0, 0).text, o.sections[0].header.paragraphs[0].text]
    assert not any(EMAIL in t for t in texts)
    assert o.core_properties.author == ""


def test_xlsx_masks_cells_and_formula_literals_keeps_numbers(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["A2"], ws["A3"], ws["A4"] = EMAIL, 42, f'="{EMAIL}"', "=A2*2"
    buf = io.BytesIO()
    wb.save(buf)
    res, out = run(tmp_path, "a.xlsx", buf.getvalue(), "t-xlsx")  # output keeps .xlsx
    assert res["success"]
    o = openpyxl.load_workbook(str(out)).active
    assert EMAIL not in o["A1"].value and EMAIL not in o["A3"].value
    assert o["A2"].value == 42 and o["A4"].value == "=A2*2"


def test_pdf_masks_text_and_metadata(tmp_path):
    d = pymupdf.open()
    d.new_page().insert_text((72, 100), f"Email {EMAIL} please", fontsize=12)
    d.set_metadata({"author": "Real Author"})
    res, out = run(tmp_path, "a.pdf", d.tobytes(), "t-pdf")
    assert res["success"]
    o = pymupdf.open(str(out))
    assert EMAIL not in o[0].get_text()
    assert not o.metadata.get("author")


def test_corrupt_file_fails_closed_without_output(tmp_path):
    res, out = run(tmp_path, "bad.docx", b"not a docx", "t-bad")
    assert res["success"] is False and "error" in res
    assert not out.exists()
