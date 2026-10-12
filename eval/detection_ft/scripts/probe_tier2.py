#!/usr/bin/env python3
"""Try the Tier 2 model on sentences YOU choose, and compare models side by side.

Run from the project root (the folder that contains dlp_core):

  # default test sentences, base model vs your fine-tuned model
  python eval/detection_ft/scripts/probe_tier2.py --model fastino/gliner2-privacy-filter-PII-multi --model models/tier2_ft_v1/final

  # your own text
  python eval/detection_ft/scripts/probe_tier2.py --model models/tier2_ft_v1/final --text "ssh deploy@prod-db-01.internal" --text "my seed phrase is ..."

Each detection prints as  LABEL 'text' (score).  No detection prints  -  nothing found.
Use --threshold 0.05 to see even very weak guesses (a very low score means the model is not just 'unsure').
"""
import argparse
import sys
from pathlib import Path

def _find_backend():
    """Locate the folder that contains the dlp_core package (repo layout or a flat copy)."""
    here = Path(__file__).resolve()
    for base in [here.parent, *here.parents, Path.cwd()]:
        if (base / "dlp_core").is_dir():
            return base
        if (base / "local-backend" / "dlp_core").is_dir():
            return base / "local-backend"
    sys.exit("could not find the dlp_core package - run this from local-backend/ or the repo root")


sys.path.insert(0, str(_find_backend()))
from dlp_core.eval.run_baseline import make_tier2  # noqa: E402

DEFAULTS = [
    "seed phrase: orchard bridge ladder comet ribbon tunnel silver garden rocket pigeon walnut saddle",
    "My recovery phrase is castle lemon tunnel orbit meadow velvet crystal harbor ember quartz tundra falcon",
    "ssh deploy_user@prod-db-01.internal",
    "scp report.pdf jsmith@bastion.corp.local:/tmp",
    "my password is Winter2026!xk and the username is sara.k",
    "the wifi key is f7Qz-91LmXa-44bT-ruNo",
    "Hi, I'm Mona Fahmy from Atlas Roofing, I live at 12 Nile Corniche, Maadi, Cairo.",
    "Patient born 14/03/1991 was admitted on 2026-02-11 after a fall.",
    "git checkout main && docker compose up -d   # request id 6c3b698f-36c7-42db-aa2a-42995db506fe",
    "The meeting is on 2026-12-31 in Cairo; the book is ISBN 978-3-16-148410-0.",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", help="model name or folder; repeat to compare several")
    ap.add_argument("--text", action="append", help="your own sentence; repeat for several")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--add", nargs="*", default=["organization", "phone_number"])
    a = ap.parse_args()
    models = a.model or ["fastino/gliner2-privacy-filter-PII-multi"]
    texts = a.text or DEFAULTS

    engines = []
    for m in models:
        eng, why = make_tier2(a.threshold, a.add, m)
        if eng is None:
            sys.exit(f"could not load {m}: {why}")
        engines.append((m, eng))

    for t in texts:
        print("\n" + t.replace("\n", "\\n"))
        for m, eng in engines:
            spans = sorted(eng.scan(t), key=lambda s: s.start)
            short = m if len(m) <= 30 else "..." + m[-27:]
            if not spans:
                print(f"  [{short}]  -  nothing found")
            for s in spans:
                print(f"  [{short}]  {s.label:<22} {t[s.start:s.end]!r}  ({s.score:.2f})")


if __name__ == "__main__":
    main()
