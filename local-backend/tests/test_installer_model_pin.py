"""Every installer pins the Tier 2 model to the same repo and a full commit sha."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ["installer/linux/install.sh", "installer/macos/install.sh", "installer/windows/install.ps1"]


def _pin(rel):
    t = (ROOT / rel).read_text(encoding="utf-8")
    return (re.search(r"Hager290/gliner2_FT", t) is not None,
            re.findall(r"\b[0-9a-f]{40}\b", t))


def test_installers_default_to_the_same_pinned_commit():
    shas = set()
    for f in FILES:
        has_repo, found = _pin(f)
        assert has_repo and len(found) == 1, f
        shas.add(found[0])
    assert len(shas) == 1


def test_fetch_model_default_repo_matches_installers():
    t = (ROOT / "scripts/fetch_model.py").read_text(encoding="utf-8")
    assert 'REPO = "Hager290/gliner2_FT"' in t
