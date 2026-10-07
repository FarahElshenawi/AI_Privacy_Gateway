"""Tier 2 / Tier 3 model bake-off. Run it on a machine where the candidate models load.

    # 1. edit candidates (see candidates.example.json), then:
    python -m dlp_core.eval.bakeoff --candidates eval/candidates.json --out eval/bakeoff --device cpu
    # 2. read eval/bakeoff/report.md ; re-analyse without re-running models:
    python -m dlp_core.eval.bakeoff --candidates eval/candidates.json --out eval/bakeoff --analyze-only

How it works
  * Every candidate runs in its OWN subprocess (a crash or OOM can't take the others down, and
    peak memory is measured per model). It writes raw predictions at a LOW score floor.
  * Thresholds are then swept offline (no re-inference): per-label operating points, the
    PERSON kill test with bootstrap CIs, and Tier 1 + candidate end-to-end combinations that go
    through the same MergeEngine production uses.
  * Nothing is decided for you. The report states the rule and the evidence, plus caveats.

What this does NOT do: download or verify models. Model ids in the example file are unverified;
check that each exists, its LICENSE, and its output format before trusting a row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..merge import MergeEngine
from ..segments import _split_long
from ..span import Span
from .metrics import _match_greedy, evaluate, report as metrics_report, wilson

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THRESHOLDS = [round(0.05 * i, 2) for i in range(2, 19)]       # 0.10 .. 0.90
KILL_TEST_PERSON_F1 = 0.97                                            # from the plan: drop SpanMarker if GLiNER >= this
RECALL_TARGET = 0.95


def text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ candidates
@dataclass
class Candidate:
    name: str
    kind: str                       # gliner | gliner2 | spanmarker | fake
    model_id: str
    labels: dict[str, str] = field(default_factory=dict)   # model label -> canonical label ("x-*" = prefix)
    floor: float = 0.05
    device: str = "cpu"
    max_chars: int = 1200           # longer texts are split at whitespace (models have ~384-512 token windows)
    notes: str = ""

    def canonical(self, model_label: str) -> Optional[str]:
        low = model_label.strip().lower()
        for k, v in self.labels.items():
            k = k.lower()
            if k == low or (k.endswith("*") and low.startswith(k[:-1])):
                return v.upper()
        return None


def load_candidates(path: str) -> list[Candidate]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Candidate(**{k: v for k, v in c.items() if k in Candidate.__dataclass_fields__}) for c in raw["candidates"]]


# ------------------------------------------------------------------ adapters (run inside the worker)
RawPred = tuple  # (start, end, canonical_label, score)


class Adapter:
    def __init__(self, cand: Candidate) -> None:
        self.cand = cand

    def load(self) -> None: ...

    def predict_piece(self, piece: str) -> list[RawPred]: ...

    def predict(self, text: str) -> list[RawPred]:
        out: list[RawPred] = []
        for off, piece in _split_long(text, self.cand.max_chars):
            for s, e, l, sc in self.predict_piece(piece):
                out.append((s + off, e + off, l, sc))
        return out


class GlinerAdapter(Adapter):
    """`gliner` package (urchade/NVIDIA/Knowledgator checkpoints): predict_entities returns char offsets."""

    def load(self) -> None:
        from gliner import GLiNER
        self.model = GLiNER.from_pretrained(self.cand.model_id)
        if self.cand.device != "cpu":
            try:
                self.model.to(self.cand.device)
            except Exception:  # noqa: BLE001
                pass
        self.prompts = list(self.cand.labels)

    def predict_piece(self, piece: str) -> list[RawPred]:
        out = []
        for e in self.model.predict_entities(piece, self.prompts, threshold=self.cand.floor):
            lab = self.cand.canonical(e["label"])
            if lab:
                out.append((int(e["start"]), int(e["end"]), lab, float(e["score"])))
        return out


class Gliner2Adapter(Adapter):
    """`gliner2` package. Reuses the production parser so offset handling is the same code path."""

    def load(self) -> None:
        from gliner2 import GLiNER2
        self.model = GLiNER2.from_pretrained(self.cand.model_id)
        self.prompts = list(self.cand.labels)

    def predict_piece(self, piece: str) -> list[RawPred]:
        from ..tier2.engine import parse_entities
        res = self.model.extract_entities_long(piece, entity_types=self.prompts, threshold=self.cand.floor,
                                               include_confidence=True, include_spans=True)
        remapped: dict[str, list] = {}
        for k, v in (res or {}).get("entities", {}).items():
            lab = self.cand.canonical(k) or k.upper()
            remapped.setdefault(lab, []).extend(v)
        spans, _ = parse_entities(piece, remapped)
        return [(s.start, s.end, s.label, s.score) for s in spans]


class SpanMarkerAdapter(Adapter):
    """`span_marker` package: predict returns dicts with char_start_index/char_end_index/label/score."""

    def load(self) -> None:
        from span_marker import SpanMarkerModel
        self.model = SpanMarkerModel.from_pretrained(self.cand.model_id)
        if self.cand.device != "cpu":
            try:
                self.model.to(self.cand.device)
            except Exception:  # noqa: BLE001
                pass

    def predict_piece(self, piece: str) -> list[RawPred]:
        out = []
        for e in self.model.predict(piece):
            lab = self.cand.canonical(str(e["label"]))
            if lab and float(e["score"]) >= self.cand.floor:
                out.append((int(e["char_start_index"]), int(e["char_end_index"]), lab, float(e["score"])))
        return out


class FakeAdapter(Adapter):
    """Deterministic stand-in used by the test-suite and for dry-running the tooling."""

    def load(self) -> None:
        import re
        from . import make_tier2_set as M
        names = "|".join(sorted(set(M.FIRST), key=len, reverse=True))
        self.person = re.compile(rf"\b(?:{names})(?: (?:{'|'.join(M.LAST)}))?\b")
        self.org = re.compile(rf"\b(?:{'|'.join(M.STEM)}) (?:{'|'.join(x for x in M.SUFFIX)})")
        self.city = re.compile(rf"\b(?:{'|'.join(M.CITIES)})\b")
        self.tool = re.compile(r"\b(?:GitHub|Kubernetes|Docker|Jenkins|Slack|Jira)\b")   # deliberate low-score false positives

    def predict_piece(self, piece: str) -> list[RawPred]:
        out = [(m.start(), m.end(), "PERSON", 0.9) for m in self.person.finditer(piece)]
        out += [(m.start(), m.end(), "ORGANIZATION", 0.85) for m in self.org.finditer(piece)]
        out += [(m.start(), m.end(), "LOCATION", 0.8) for m in self.city.finditer(piece)]
        out += [(m.start(), m.end(), "ORGANIZATION", 0.25) for m in self.tool.finditer(piece)]
        return out


ADAPTERS = {"gliner": GlinerAdapter, "gliner2": Gliner2Adapter, "spanmarker": SpanMarkerAdapter, "fake": FakeAdapter}


# ------------------------------------------------------------------ worker
def _rss_mb() -> Optional[float]:
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(rss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except Exception:  # noqa: BLE001
        return None


def load_cases(paths: list[str]) -> list[dict]:
    cases = []
    for p in paths:
        cases += [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
    return cases


def run_worker(cand: Candidate, set_paths: list[str], out_path: str) -> None:
    result: dict[str, Any] = {"candidate": asdict(cand), "ok": False, "error": None}
    try:
        t0 = time.perf_counter()
        ad = ADAPTERS[cand.kind](cand)
        ad.load()
        result["load_s"] = round(time.perf_counter() - t0, 2)
        texts = sorted({c["text"] for c in load_cases(set_paths)})
        for t in texts[:3]:                              # warm-up (first calls are slow: kernels, caches)
            ad.predict(t)
        preds, lat, errors = {}, [], 0
        for t in texts:
            t1 = time.perf_counter()
            try:
                preds[text_key(t)] = [list(p) for p in ad.predict(t)]
            except Exception:  # noqa: BLE001
                errors += 1
                preds[text_key(t)] = []
            lat.append({"chars": len(t), "ms": (time.perf_counter() - t1) * 1e3})
        result.update(ok=True, preds=preds, latencies=lat, predict_errors=errors, rss_mb=_rss_mb())
    except Exception as exc:  # noqa: BLE001 - report, never crash the orchestrator
        result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    Path(out_path).write_text(json.dumps(result), encoding="utf-8")


# ------------------------------------------------------------------ analysis
def make_detect(sources: list[tuple[dict, Any]], tier1: bool):
    """sources: [(preds_by_text_key, threshold_or_per_label_dict)]. Same MergeEngine as production."""
    from ..tier1 import Tier1Engine
    t1 = Tier1Engine() if tier1 else None
    merge = MergeEngine()

    def detect(text: str):
        spans = list(t1.scan(text)) if t1 else []
        k = text_key(text)
        for preds, thr in sources:
            for s, e, label, score in preds.get(k, ()):
                floor = thr.get(label, thr.get("*", 0.3)) if isinstance(thr, dict) else thr
                if score >= floor and 0 <= s < e <= len(text):
                    spans.append(Span(s, e, label, min(1.0, max(0.0, score)), "tier2.candidate"))
        return merge.merge(spans, text)
    return detect


def sweep(preds: dict, cases: list[dict], thresholds: list[float], tiers: set[int]) -> dict[float, dict]:
    return {t: metrics_report(evaluate(cases, make_detect([(preds, t)], tier1=False), tiers)) for t in thresholds}


def recommended_thresholds(sw: dict[float, dict], labels: list[str]) -> dict:
    """Per label: the highest threshold that still meets the recall target, minus ONE grid step of
    margin (the extreme point sits on the edge of the data and would be brittle). Flags labels
    that can't reach the target at any threshold; those get the lowest threshold."""
    rec, unmet = {}, []
    grid = sorted(sw)
    for lab in labels:
        ok = [t for t, r in sw.items() if (r["per_label"].get(lab, {}).get("char_recall") or 0) >= RECALL_TARGET]
        if ok:
            i = grid.index(max(ok))
            rec[lab] = grid[max(0, i - 1)]
        else:
            rec[lab] = min(sw)
            unmet.append(lab)
    return {"min_scores": rec, "recall_target_unmet": unmet}


def per_case_counts(cases: list[dict], detect, label: str, tiers: set[int]) -> list[tuple[int, int, int]]:
    out = []
    for c in cases:
        gold = [g for g in c["spans"] if g["label"] == label and g.get("tier", 1) in tiers]
        preds = [p for p in detect(c["text"]) if p.label == label]
        tp, fp, fn = _match_greedy(preds, gold, need_label=True)
        out.append((tp, fp, fn))
    return out


def f1_of(counts) -> float:
    tp, fp, fn = (sum(x[i] for x in counts) for i in range(3))
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0


def bootstrap_ci(counts, n: int = 1000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    vals = sorted(f1_of([counts[rng.randrange(len(counts))] for _ in counts]) for _ in range(n))
    return round(vals[int(0.025 * n)], 3), round(vals[int(0.975 * n)], 3)


def pct(vals: list[float], q: float) -> Optional[float]:
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None


def analyze(out_dir: Path, cand_names: list[str], tier2_path: str, e2e_paths: list[str],
            thresholds: Optional[list[float]] = None) -> dict:
    thresholds = thresholds or DEFAULT_THRESHOLDS
    t2_cases = load_cases([tier2_path])
    e2e_cases = load_cases(e2e_paths + [tier2_path])
    res: dict[str, Any] = {"candidates": {}, "failed": {}, "kill_test": {}, "combos": {}, "caveats": []}
    loaded: dict[str, dict] = {}
    for name in cand_names:
        data = json.loads((out_dir / f"preds_{name}.json").read_text()) if (out_dir / f"preds_{name}.json").exists() else None
        if not data or not data.get("ok"):
            res["failed"][name] = (data or {}).get("error") or "no result file (worker did not run)"
            continue
        loaded[name] = data
    for name, data in loaded.items():
        preds, cand = data["preds"], data["candidate"]
        sw = sweep(preds, t2_cases, thresholds, {2, 3})
        labels = sorted({g["label"] for c in t2_cases for g in c["spans"]})
        best_t = max(sw, key=lambda t: sw[t]["overlap"]["f1"])
        rec = recommended_thresholds(sw, labels)
        lat = data.get("latencies", [])
        chars = sum(x["chars"] for x in lat) or 1
        res["candidates"][name] = {
            "kind": cand["kind"], "model_id": cand["model_id"], "load_s": data.get("load_s"), "rss_mb": data.get("rss_mb"),
            "predict_errors": data.get("predict_errors", 0),
            "latency_ms": {"p50": pct([x["ms"] for x in lat], .5), "p95": pct([x["ms"] for x in lat], .95),
                           "ms_per_1k_chars": round(sum(x["ms"] for x in lat) / chars * 1000, 1)},
            "default_0.3": _brief(sw.get(0.3) or sw[min(sw, key=lambda t: abs(t - 0.3))]),
            "best_overlap_f1": {"threshold": best_t, **_brief(sw[best_t])},
            "recommended": rec, "sweep": {str(t): _brief(r) for t, r in sw.items()},
            "per_label_at_best": sw[best_t]["per_label"],
        }
        person_t = rec["min_scores"].get("PERSON", best_t)
        counts = per_case_counts(t2_cases, make_detect([(preds, person_t)], False), "PERSON", {2, 3})
        f1 = round(f1_of(counts), 3)
        res["kill_test"][name] = {"kind": cand["kind"], "person_threshold": person_t, "person_f1": f1,
                                  "ci95": bootstrap_ci(counts), "passes_rule": f1 >= KILL_TEST_PERSON_F1}
        # end-to-end: Tier 1 + this candidate at its recommended thresholds, on every set, all tiers in scope
        e2e = metrics_report(evaluate(e2e_cases, make_detect([(preds, rec["min_scores"])], tier1=True), None))
        res["combos"][name] = {"members": [name], **_brief(e2e), "leak_recall": e2e["leak_recall_overall"]["char_recall"]}
    gl = [n for n, c in res["candidates"].items() if c["kind"] in ("gliner", "gliner2")]
    sm = [n for n, c in res["candidates"].items() if c["kind"] == "spanmarker"]
    if gl and sm:
        bg = max(gl, key=lambda n: res["kill_test"][n]["person_f1"])
        for s in sm:
            srcs = [(loaded[bg]["preds"], res["candidates"][bg]["recommended"]["min_scores"]),
                    (loaded[s]["preds"], res["candidates"][s]["recommended"]["min_scores"])]
            e2e = metrics_report(evaluate(e2e_cases, make_detect(srcs, tier1=True), None))
            res["combos"][f"{bg}+{s}"] = {"members": [bg, s], **_brief(e2e), "leak_recall": e2e["leak_recall_overall"]["char_recall"]}
        best_gl = res["kill_test"][bg]
        res["kill_test_verdict"] = {
            "best_gliner_candidate": bg, "person_f1": best_gl["person_f1"], "ci95": best_gl["ci95"],
            "rule": f"drop SpanMarker only if best GLiNER-type PERSON F1 >= {KILL_TEST_PERSON_F1}",
            "gliner_meets_rule": best_gl["passes_rule"],
            "spanmarker_f1": {s: res["kill_test"][s]["person_f1"] for s in sm}}
    res["caveats"] = [
        "Gold labels were written by the same author as the detectors; the tier2 set is synthetic, so scores are optimistic.",
        "Per-label counts of 30-100 give +/-0.05 to +/-0.10 uncertainty; do not chase differences smaller than the CIs.",
        "Public tool names are gold-negative (a product decision); change make_tier2_set.py if yours differs.",
        "Model ids/licenses are unverified by the tooling author; verify before adoption.",
        "Latency/memory were measured on THIS machine, one request at a time, not under load.",
    ]
    return res


def _brief(r: dict) -> dict:
    return {"strict_f1": r["strict"]["f1"], "overlap_f1": r["overlap"]["f1"], "overlap_p": r["overlap"]["precision"],
            "overlap_r": r["overlap"]["recall"], "leak_recall": r["leak_recall_overall"]["char_recall"],
            "benign_fp_case_rate": r["benign"]["fp_case_rate"], "benign_cases_with_fp": r["benign"]["cases_with_fp"]}


def render_markdown(res: dict) -> str:
    L = ["# Tier 2 / Tier 3 bake-off report", ""]
    if res["failed"]:
        L += ["## Candidates that did not run", ""] + [f"- **{n}**: `{e}`" for n, e in res["failed"].items()] + [""]
    L += ["## Cost (this machine)", "", "| candidate | kind | load s | RSS MB | p50 ms | p95 ms | ms / 1k chars | predict errors |", "|---|---|---|---|---|---|---|---|"]
    for n, c in res["candidates"].items():
        lt = c["latency_ms"]
        L.append(f"| {n} | {c['kind']} | {c['load_s']} | {c['rss_mb']} | {lt['p50']} | {lt['p95']} | {lt['ms_per_1k_chars']} | {c['predict_errors']} |")
    L += ["", "## Standalone quality on tier2_v1 (tiers 2+3 in scope)", "",
          "| candidate | @0.3 overlap F1 | @0.3 leak recall | best thr | best overlap F1 | P | R | leak recall | benign FP cases |", "|---|---|---|---|---|---|---|---|---|"]
    for n, c in res["candidates"].items():
        d, b = c["default_0.3"], c["best_overlap_f1"]
        L.append(f"| {n} | {d['overlap_f1']} | {d['leak_recall']} | {b['threshold']} | {b['overlap_f1']} | {b['overlap_p']} | {b['overlap_r']} | {b['leak_recall']} | {b['benign_cases_with_fp']} |")
    L += ["", f"## Recommended per-label thresholds (highest threshold keeping char recall >= {RECALL_TARGET}, minus one 0.05 step of margin)", ""]
    for n, c in res["candidates"].items():
        rec = c["recommended"]
        L.append(f"- **{n}**: `{json.dumps(rec['min_scores'])}`" + (f"  (target NOT met: {', '.join(rec['recall_target_unmet'])})" if rec["recall_target_unmet"] else ""))
    L += ["", "## PERSON kill test", ""]
    L += ["| candidate | kind | PERSON thr | PERSON F1 | 95% CI | meets rule |", "|---|---|---|---|---|---|"]
    for n, k in res["kill_test"].items():
        L.append(f"| {n} | {k['kind']} | {k['person_threshold']} | {k['person_f1']} | {k['ci95']} | {'yes' if k['passes_rule'] else 'no'} |")
    v = res.get("kill_test_verdict")
    if v:
        L += ["", f"Rule: {v['rule']}. Best GLiNER-type: **{v['best_gliner_candidate']}** at {v['person_f1']} (CI {v['ci95']}); "
                  f"SpanMarker: {v['spanmarker_f1']}. Meets rule: **{'yes' if v['gliner_meets_rule'] else 'no'}**. "
                  "If the CI straddles the rule, the data cannot decide: keep SpanMarker until the set is larger."]
    L += ["", "## End to end: Tier 1 + candidate(s) on every set (all tiers in scope)", "",
          "| combination | leak recall | overlap F1 | precision | benign FP cases |", "|---|---|---|---|---|"]
    for n, c in res["combos"].items():
        L.append(f"| {n} | {c['leak_recall']} | {c['overlap_f1']} | {c['overlap_p']} | {c['benign_cases_with_fp']} |")
    L += ["", "## Caveats", ""] + [f"- {c}" for c in res["caveats"]]
    L += ["", "## Per-label detail at each candidate's best threshold", ""]
    for n, c in res["candidates"].items():
        L += [f"**{n}**", "", "| label | strict P | R | F1 | char recall | FPs |", "|---|---|---|---|---|---|"]
        for lab, v in c["per_label_at_best"].items():
            if v["spans"]:
                L.append(f"| {lab} | {v['strict_p']} | {v['strict_r']} | {v['strict_f1']} | {v['char_recall']} | {v['fp']} |")
        L.append("")
    return "\n".join(L)


# ------------------------------------------------------------------ orchestration
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", default="eval/bakeoff")
    ap.add_argument("--tier2-set", default="eval/tier2_v1.jsonl")
    ap.add_argument("--e2e-sets", nargs="*", default=["eval/holdout_v1.jsonl", "eval/dev_v1.jsonl"])
    ap.add_argument("--device", default=None, help="cpu|cuda|mps (overrides the candidates file)")
    ap.add_argument("--only", nargs="*", help="run only these candidate names")
    ap.add_argument("--rerun", action="store_true", help="ignore cached predictions")
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    ap.add_argument("--worker-out", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    cands = load_candidates(a.candidates)
    sets = [a.tier2_set, *a.e2e_sets]
    if a.worker:
        cand = next(c for c in cands if c.name == a.worker)
        if a.device:
            cand.device = a.device
        run_worker(cand, sets, a.worker_out)
        return 0

    from .run import frozen_check
    missing = [s for s in sets if not Path(s).exists()]
    if missing:
        print(f"missing input file(s): {missing}\nrun from the local-backend folder; generate sets with "
              "`python -m dlp_core.eval.make_tier2_set eval`, `make_holdout`, `make_dev`.", file=sys.stderr)
        return 2
    for s in sets:
        if Path(s).with_suffix(".sha256").exists():
            frozen_check(s)                       # refuse to score on a modified frozen set
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    names = [c.name for c in cands if not a.only or c.name in a.only]
    if not a.analyze_only:
        for name in names:
            pf = out / f"preds_{name}.json"
            if pf.exists() and not a.rerun:
                print(f"[{name}] cached"); continue
            print(f"[{name}] running ...", flush=True)
            cmd = [sys.executable, "-m", "dlp_core.eval.bakeoff", "--candidates", a.candidates, "--worker", name,
                   "--worker-out", str(pf), "--tier2-set", a.tier2_set, "--e2e-sets", *a.e2e_sets]
            if a.device:
                cmd += ["--device", a.device]
            p = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
            if p.returncode != 0 and not pf.exists():
                pf.write_text(json.dumps({"ok": False, "error": f"worker crashed (exit {p.returncode}): {p.stderr[-300:]}"}))
    res = analyze(out, names, a.tier2_set, a.e2e_sets)
    (out / "report.json").write_text(json.dumps(res, indent=2))
    md = render_markdown(res)
    (out / "report.md").write_text(md)
    print(md[:3000])
    print(f"\nfull report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
