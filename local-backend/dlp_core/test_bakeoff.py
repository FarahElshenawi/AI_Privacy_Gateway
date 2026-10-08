"""Bake-off tooling tests. No real models: stub modules emulate the documented APIs of the
`gliner`, `gliner2` and `span_marker` packages, so these prove OUR adapters, parsing, sweeps and
reports, not any model's quality or the exact API of a package version you install."""
import json
import sys
import types
from pathlib import Path

import pytest

from dlp_core.eval import bakeoff as B

ROOT = Path(__file__).resolve().parents[1]
T2 = str(ROOT / "eval" / "tier2_v1.jsonl")
E2E = [str(ROOT / "eval" / "holdout_v1.jsonl"), str(ROOT / "eval" / "dev_v1.jsonl")]


# The frozen eval sets are generated data that is not committed; skip (don't fail) without them.
pytestmark = pytest.mark.skipif(
    not all(Path(f).exists() for f in [T2, *E2E, str(ROOT / "eval" / "candidates.example.json")]),
    reason="bake-off eval data (tier2_v1/holdout_v1/dev_v1/candidates.example) not present",
)


def write_cands(tmp_path, entries):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"candidates": entries}))
    return str(p)


def fake(name="fake_a", kind="fake"):
    return {"name": name, "kind": kind, "model_id": "x", "labels": {"PERSON": "PERSON"}}


def run_main(tmp_path, entries, *extra):
    out = tmp_path / "out"
    rc = B.main(["--candidates", write_cands(tmp_path, entries), "--out", str(out), "--tier2-set", T2, "--e2e-sets", *E2E, *extra])
    return rc, out


# ---- orchestration ------------------------------------------------------------------
def test_end_to_end_report_with_fake_candidate_and_a_failing_one(tmp_path):
    rc, out = run_main(tmp_path, [fake(), {"name": "broken", "kind": "gliner", "model_id": "no/such", "labels": {"person": "PERSON"}}])
    assert rc == 0
    res = json.loads((out / "report.json").read_text())
    assert "fake_a" in res["candidates"] and "broken" in res["failed"]
    c = res["candidates"]["fake_a"]
    assert c["rss_mb"] and c["latency_ms"]["p95"] is not None and len(c["sweep"]) == len(B.DEFAULT_THRESHOLDS)
    md = (out / "report.md").read_text()
    for section in ("Candidates that did not run", "Cost", "PERSON kill test", "End to end", "Caveats"):
        assert section in md


def test_cached_predictions_are_reused_and_rerun_forces_new(tmp_path):
    rc, out = run_main(tmp_path, [fake()])
    pf = out / "preds_fake_a.json"
    m1 = pf.stat().st_mtime_ns
    run_main(tmp_path, [fake()])
    assert pf.stat().st_mtime_ns == m1
    run_main(tmp_path, [fake()], "--rerun")
    assert pf.stat().st_mtime_ns != m1


def test_missing_input_files_exit_with_a_clear_code(tmp_path, capsys):
    rc = B.main(["--candidates", write_cands(tmp_path, [fake()]), "--out", str(tmp_path / "o"), "--tier2-set", "nope.jsonl", "--e2e-sets"])
    assert rc == 2 and "missing input" in capsys.readouterr().err


def test_modified_frozen_set_is_refused(tmp_path):
    t2 = tmp_path / "tier2_v1.jsonl"
    t2.write_text(Path(T2).read_text())
    (tmp_path / "tier2_v1.sha256").write_text("0" * 64 + "\n")
    with pytest.raises(SystemExit):
        B.main(["--candidates", write_cands(tmp_path, [fake()]), "--out", str(tmp_path / "o"), "--tier2-set", str(t2), "--e2e-sets"])


# ---- analysis ---------------------------------------------------------------------------
def test_threshold_sweep_removes_low_score_false_positives_without_losing_recall(tmp_path):
    rc, out = run_main(tmp_path, [fake()])
    res = json.loads((out / "report.json").read_text())["candidates"]["fake_a"]
    low, high = res["sweep"]["0.1"], res["sweep"]["0.5"]
    assert low["benign_cases_with_fp"] > 0 and high["benign_cases_with_fp"] == 0       # tool-name FPs (score .25) vanish
    assert high["overlap_r"] == low["overlap_r"]                                        # true entities (score >= .8) unaffected
    rec = res["recommended"]["min_scores"]
    assert rec["ORGANIZATION"] >= 0.25 and rec["PERSON"] <= 0.9


def test_recommended_threshold_keeps_one_step_of_margin():
    sw = {t: {"per_label": {"X": {"char_recall": 1.0 if t <= 0.5 else 0.5}}} for t in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]}
    assert B.recommended_thresholds(sw, ["X"])["min_scores"]["X"] == 0.4
    sw2 = {t: {"per_label": {"X": {"char_recall": 0.2}}} for t in [0.1, 0.2]}
    r = B.recommended_thresholds(sw2, ["X"])
    assert r["min_scores"]["X"] == 0.1 and r["recall_target_unmet"] == ["X"]


def test_bootstrap_is_deterministic_and_bracketing():
    counts = [(1, 0, 0)] * 80 + [(0, 1, 1)] * 20
    f = B.f1_of(counts)
    lo, hi = B.bootstrap_ci(counts)
    assert lo <= f <= hi and B.bootstrap_ci(counts) == (lo, hi)


def test_kill_test_verdict_and_combo_when_both_model_kinds_present(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    for name, kind in (("gl", "gliner"), ("sm", "spanmarker")):
        B.run_worker(B.Candidate(name, "fake", "x", {"PERSON": "PERSON"}), [T2, *E2E], str(out / f"preds_{name}.json"))
        d = json.loads((out / f"preds_{name}.json").read_text()); d["candidate"]["kind"] = kind
        (out / f"preds_{name}.json").write_text(json.dumps(d))
    res = B.analyze(out, ["gl", "sm"], T2, E2E)
    v = res["kill_test_verdict"]
    assert v["best_gliner_candidate"] == "gl" and "gl+sm" in res["combos"] and v["rule"].startswith("drop SpanMarker only if")
    assert isinstance(v["gliner_meets_rule"], bool) and len(v["ci95"]) == 2
    assert "straddles" in B.render_markdown(res)


def test_end_to_end_combo_includes_tier1_so_structured_pii_still_counts(tmp_path):
    rc, out = run_main(tmp_path, [fake()])
    res = json.loads((out / "report.json").read_text())
    assert res["combos"]["fake_a"]["leak_recall"] > 0.5        # holdout/dev structured PII is caught by Tier 1


# ---- adapters against stubbed package APIs -----------------------------------------------
@pytest.fixture
def stubs(monkeypatch):
    calls = {}

    class GLiNER:
        @classmethod
        def from_pretrained(cls, mid):
            calls["gliner_id"] = mid
            return cls()

        def predict_entities(self, text, labels, threshold=0.5):
            calls.setdefault("gliner_labels", labels); calls["gliner_thr"] = threshold
            i = text.find("Ann Lee")
            return [{"start": i, "end": i + 7, "text": "Ann Lee", "label": "person", "score": 0.9},
                    {"start": 0, "end": 1, "text": text[:1], "label": "ignored", "score": 0.9}] if i >= 0 else []

    class GLiNER2:
        @classmethod
        def from_pretrained(cls, mid):
            return cls()

        def extract_entities_long(self, text, **kw):
            assert kw["include_spans"] and kw["entity_types"]
            return {"entities": {"full_name": [{"text": "Ann Lee", "confidence": 0.8}]}}

    class SpanMarkerModel:
        @classmethod
        def from_pretrained(cls, mid):
            return cls()

        def predict(self, text):
            i = text.find("Ann Lee")
            return [{"span": "Ann Lee", "label": "per-x", "score": 0.95, "char_start_index": i, "char_end_index": i + 7},
                    {"span": "x", "label": "per-x", "score": 0.01, "char_start_index": 0, "char_end_index": 1}] if i >= 0 else []

    for mod, attr, obj in (("gliner", "GLiNER", GLiNER), ("gliner2", "GLiNER2", GLiNER2), ("span_marker", "SpanMarkerModel", SpanMarkerModel)):
        m = types.ModuleType(mod); setattr(m, attr, obj); monkeypatch.setitem(sys.modules, mod, m)
    return calls


def test_gliner_adapter_maps_labels_adjusts_offsets_across_split_pieces(stubs):
    cand = B.Candidate("g", "gliner", "org/model", {"person": "PERSON"}, floor=0.1, max_chars=40)
    ad = B.GlinerAdapter(cand); ad.load()
    text = ("filler words " * 8) + "then Ann Lee spoke"
    out = ad.predict(text)
    assert [(text[s:e], l) for s, e, l, _ in out] == [("Ann Lee", "PERSON")]
    assert stubs["gliner_id"] == "org/model" and stubs["gliner_labels"] == ["person"] and stubs["gliner_thr"] == 0.1


def test_gliner2_adapter_uses_production_parser_all_occurrences(stubs):
    ad = B.Gliner2Adapter(B.Candidate("g2", "gliner2", "m", {"full_name": "PERSON"})); ad.load()
    text = "Ann Lee met Ann Lee."
    assert [(s, e, l) for s, e, l, _ in ad.predict(text)] == [(0, 7, "PERSON"), (12, 19, "PERSON")]


def test_spanmarker_adapter_prefix_labels_and_floor(stubs):
    ad = B.SpanMarkerAdapter(B.Candidate("s", "spanmarker", "m", {"per-*": "PERSON"}, floor=0.05)); ad.load()
    assert ad.predict("hi Ann Lee")[0][2] == "PERSON" and len(ad.predict("hi Ann Lee")) == 1       # 0.01 < floor dropped


def test_example_candidates_file_is_valid_and_labels_resolve():
    cs = B.load_candidates(str(ROOT / "eval" / "candidates.example.json"))
    assert len(cs) >= 5 and all(c.canonical(next(iter(c.labels))) for c in cs)
    sm = [c for c in cs if c.kind == "spanmarker" and "fewnerd" in c.name][0]
    assert sm.canonical("person-politician") == "PERSON" and sm.canonical("art-music") is None
