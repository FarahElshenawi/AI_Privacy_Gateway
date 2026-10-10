#!/usr/bin/env python3
"""Download the Tier 2 model at ONE pinned revision into a local directory.

    python scripts/fetch_model.py --revision <40-char commit sha> [--dest local-backend/models/gliner2-pii]

The backend then loads it from disk (DLP_TIER2_MODEL=<dest>), so nothing is fetched at run time and
the model cannot change underneath a deployment. Refuses a branch name or short hash: only a full
commit sha is a pin. Writes MODEL_REVISION next to the weights.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = "fastino/gliner2-privacy-filter-PII-multi"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revision", required=True, help="full 40-character commit sha")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--dest", default="local-backend/models/gliner2-pii")
    a = ap.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", a.revision):
        print("error: --revision must be a full 40-character commit sha, not a branch or tag", file=sys.stderr)
        return 2
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("error: pip install huggingface_hub", file=sys.stderr)
        return 2
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    path = Path(snapshot_download(repo_id=a.repo, revision=a.revision, local_dir=str(dest)))
    (path / "MODEL_REVISION").write_text(f"{a.repo}@{a.revision}\n")
    print(f"ok: {a.repo}@{a.revision} -> {path}\nset DLP_TIER2_MODEL={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
