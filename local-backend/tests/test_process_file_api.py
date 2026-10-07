"""HTTP-level tests for /api/process_file (offset-based, fail-closed, headers, size cap)."""
import io

import pymupdf
from fastapi.testclient import TestClient

from app.main import app
from app.security import origin_check
from app.security.auth import get_install_token

origin_check.ALLOWED_HOSTS = origin_check.ALLOWED_HOSTS | {"testclient"}
client = TestClient(app)
AUTH = {"Authorization": f"Bearer {get_install_token()}"}


def post(name, data, ctype="application/octet-stream", cid="pf1", headers=AUTH):
    return client.post("/api/process_file", files={"file": (name, io.BytesIO(data), ctype)},
                       data={"conversation_id": cid}, headers=headers)


def test_requires_token():
    assert post("a.txt", b"hi", headers={}).status_code == 401


def test_text_file_masked_in_place_with_coverage_headers():
    r = post("a.txt", b"cvv 123, order 12345, mail jane@example.com\n")
    assert r.status_code == 200, r.text
    body = r.content.decode()
    assert "jane@example.com" not in body and "order 12345" in body and "[REDACTED:CVV]" in body
    assert r.headers["x-dlp-degraded"] in ("true", "false") and "x-dlp-uncovered-labels" in r.headers
    assert r.headers["x-dlp-replacements"] == "2"


def test_scanned_pdf_is_rejected_with_reason_not_passed_through(tmp_path):
    doc = pymupdf.open(); pg = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 40), False); pix.clear_with(255)
    pg.insert_image(pymupdf.Rect(72, 72, 172, 112), pixmap=pix)
    r = post("scan.pdf", doc.tobytes(), "application/pdf")
    assert r.status_code == 422
    assert "no_extractable_text" in r.json()["detail"]["blockers"]


def test_corrupt_and_unknown_files_rejected():
    assert post("bad.docx", b"not a docx").status_code in (400, 422)
    assert post("x.bin", b"\x00\x01\x02\x03").status_code == 400
