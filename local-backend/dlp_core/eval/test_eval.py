import hashlib
from dataclasses import dataclass

from dlp_core.eval import make_holdout
from dlp_core.eval.metrics import evaluate, report, wilson


@dataclass
class P:
    start: int
    end: int
    label: str


CASES = [
    {"id": "a", "text": "mail a@b.com now", "tags": ["x"], "spans": [{"start": 5, "end": 12, "label": "EMAIL", "tier": 1}]},
    {"id": "b", "text": "benign text 123", "tags": ["benign"], "spans": []},
    {"id": "c", "text": "Ann lives here", "tags": [], "spans": [{"start": 0, "end": 3, "label": "PERSON", "tier": 3}]},
]


def test_partial_cover_counts_as_leak_not_hit():
    rep = report(evaluate(CASES, lambda t: [P(5, 9, "EMAIL")] if "mail" in t else [], tiers={1}))
    assert rep["leak_recall_overall"]["char_recall"] == round(4 / 7, 4)
    assert rep["leak_recall_overall"]["partial"] == 1 and rep["strict"]["fn"] == 1


def test_scope_excludes_other_tier_but_false_positives_still_count():
    rep = report(evaluate(CASES, lambda t: [P(0, 3, "PERSON")] if t.startswith("Ann") else [P(0, 4, "X")], tiers={1}))
    assert rep["out_of_scope_gold_spans"] == 1
    assert rep["benign"]["cases_with_fp"] == 1                       # benign FP never hidden
    assert rep["strict"]["fp"] == 2                                  # two stray X spans; PERSON on tier-3 gold is not an FP


def test_perfect_and_empty():
    rep = report(evaluate(CASES[:1], lambda t: [P(5, 12, "EMAIL")], tiers={1}))
    assert rep["strict"]["f1"] == 1.0 and rep["leak_recall_overall"]["char_recall"] == 1.0
    assert wilson(0, 0) == (0.0, 0.0)


def test_holdout_is_deterministic_and_offsets_valid(tmp_path):
    make_holdout.rng.seed(make_holdout.SEED)
    make_holdout.main(tmp_path / "a")
    make_holdout.rng.seed(make_holdout.SEED)
    make_holdout.main(tmp_path / "b")
    a = (tmp_path / "a" / "holdout_v1.jsonl").read_bytes()
    assert a == (tmp_path / "b" / "holdout_v1.jsonl").read_bytes()
    import json
    for line in a.decode().splitlines():
        c = json.loads(line)
        assert all(0 <= s["start"] < s["end"] <= len(c["text"]) for s in c["spans"])
