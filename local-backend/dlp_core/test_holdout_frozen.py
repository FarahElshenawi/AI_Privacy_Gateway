"""The hold-out set is frozen: the generator must reproduce bytes whose sha256 equals the recorded hash.
If this fails, someone changed the set that thresholds are judged on (or the generator's output)."""
import hashlib
from pathlib import Path


def test_generator_reproduces_the_frozen_holdout(tmp_path):
    from dlp_core.eval import make_holdout
    make_holdout.main(tmp_path)
    recorded = (Path(__file__).parent / "eval" / "holdout_v1.sha256").read_text().strip()
    assert hashlib.sha256((tmp_path / "holdout_v1.jsonl").read_bytes()).hexdigest() == recorded
