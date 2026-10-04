import fitz

from app.multimodal.reconstructors.pdf_reconstructor import PDFReconstructor


def _make_pdf(path, text):
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), text, fontname="helv", fontsize=12)
    doc.save(path)
    doc.close()


def test_pdf_reconstructor_uses_supplied_pairs(tmp_path):
    src, out = str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")
    _make_pdf(src, "Contact Jane Doe at jane@corp.com. Farah is not PII here.")
    PDFReconstructor().reconstruct(
        {"metadata": {"path": src}},
        [("Jane Doe", "Mary Roe"), ("jane@corp.com", "mary@x.org")],
        out,
    )
    text = fitz.open(out)[0].get_text()
    assert "Jane Doe" not in text and "jane@corp.com" not in text
    assert "Mary Roe" in text and "mary@x.org" in text
    assert "Farah" in text  # no hard-coded name replacement anymore
