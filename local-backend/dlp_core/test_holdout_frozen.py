"""The hold-out set is frozen: a fresh run of the generator must produce bytes whose sha256 equals the
recorded hash. If this fails, someone changed the set that thresholds are judged on (or the generator).

Runs in a subprocess because the generator draws from a module-level RNG at import time, so the
output is only defined for a freshly imported module."""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generator_reproduces_the_frozen_holdout(tmp_path):
    subprocess.run([sys.executable, "-m", "dlp_core.eval.make_holdout", str(tmp_path)],
                   cwd=ROOT, check=True, capture_output=True)
    recorded = (Path(__file__).parent / "eval" / "holdout_v1.sha256").read_text().strip()
    assert hashlib.sha256((tmp_path / "holdout_v1.jsonl").read_bytes()).hexdigest() == recorded
