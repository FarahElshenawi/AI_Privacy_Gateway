"""Evaluation harness for the detection engine.

Measures:
  - Strict F1:        exact span match (start, end, label)
  - Overlap F1:       span overlaps at all (any overlap counts as TP)
  - Type-agnostic F1:  span matched at all (label doesn't matter)
  - Per-label P/R/F1:  precision, recall, F1 for each entity type
  - Confidence intervals: Wilson 95% CI on each score

Usage:
    python -m eval.run_eval --set eval/eval_set_v1.jsonl --tier1
    python -m eval.run_eval --set eval/eval_set_v1.jsonl --tier1 --dump-fp
    python -m eval.run_eval --set eval/eval_set_v1.jsonl --tier1 --dump-fn

Output:
    Baseline report with per-label scores, coverage, and confidence intervals.
    False positives / false negatives dumped to JSONL for inspection.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Add parent dir to path so we can import the engine
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline.tier1 import ComplianceValidatorEngine
from app.pipeline.tier1.types import Span


# ─── Data structures ──────────────────────────────────────────

@dataclass(frozen=True)
class GoldSpan:
    start: int
    end: int
    label: str

    def overlaps(self, other: "GoldSpan") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass
class EvalResult:
    label: str = ""
    tp: int = 0  # true positives
    fp: int = 0  # false positives
    fn: int = 0  # false negatives

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def wilson_ci(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% confidence interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0, center - spread), min(1, center + spread))


# ─── Loading ─────────────────────────────────────────────────

def load_eval_set(path: str) -> list[dict]:
    """Load JSONL eval set. Each line: {"text": str, "spans": [{start, end, label}]}"""
    examples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ex = json.loads(line)
            ex["gold_spans"] = [
                GoldSpan(s["start"], s["end"], s["label"])
                for s in ex.get("spans", [])
            ]
            examples.append(ex)
    return examples


# ─── Matching logic ──────────────────────────────────────────

def match_strict(pred: list[Span], gold: list[GoldSpan]) -> tuple[list, list, list]:
    """Strict match: exact (start, end, label) tuple."""
    pred_set = {(s.start, s.end, s.label) for s in pred}
    gold_set = {(g.start, g.end, g.label) for g in gold}
    tp = pred_set & gold_set
    fp = pred_set - gold_set
    fn = gold_set - pred_set
    return list(tp), list(fp), list(fn)


def match_overlap(pred: list[Span], gold: list[GoldSpan]) -> tuple[int, int, int]:
    """Overlap match: any overlap counts as TP. Greedy assignment."""
    unmatched_gold = list(gold)
    tp = 0
    for p in pred:
        for i, g in enumerate(unmatched_gold):
            if GoldSpan(p.start, p.end, p.label).overlaps(g):
                tp += 1
                unmatched_gold.pop(i)
                break
    fp = len(pred) - tp
    fn = len(unmatched_gold)
    return tp, fp, fn


def match_type_agnostic(pred: list[Span], gold: list[GoldSpan]) -> tuple[int, int, int]:
    """Type-agnostic: span matches if it overlaps any gold span (label ignored)."""
    unmatched_gold = list(gold)
    tp = 0
    for p in pred:
        for i, g in enumerate(unmatched_gold):
            if p.start < g.end and g.start < p.end:
                tp += 1
                unmatched_gold.pop(i)
                break
    fp = len(pred) - tp
    fn = len(unmatched_gold)
    return tp, fp, fn


# ─── Main eval ───────────────────────────────────────────────

def run_eval(eval_set_path: str, dump_fp: bool = False, dump_fn: bool = False) -> dict:
    """Run evaluation and return a report dict."""
    examples = load_eval_set(eval_set_path)
    engine = ComplianceValidatorEngine()

    # Per-label results (strict)
    per_label: dict[str, EvalResult] = defaultdict(EvalResult)

    # Aggregate counts (for strict, overlap, type-agnostic F1)
    strict_tp = strict_fp = strict_fn = 0
    overlap_tp = overlap_fp = overlap_fn = 0
    type_agnostic_tp = type_agnostic_fp = type_agnostic_fn = 0

    fp_dump = []
    fn_dump = []

    for ex_idx, ex in enumerate(examples):
        text = ex["text"]
        gold = ex["gold_spans"]

        # Run Tier 1
        pred_spans = engine.scan_raw(text)

        # Strict match
        tp_s, fp_s, fn_s = match_strict(pred_spans, gold)
        strict_tp += len(tp_s)
        strict_fp += len(fp_s)
        strict_fn += len(fn_s)

        # Per-label strict
        for t in tp_s:
            per_label[t[2]].tp += 1
        for f in fp_s:
            per_label[f[2]].fp += 1
        for f in fn_s:
            per_label[f[2]].fn += 1

        # Overlap match
        tp_o, fp_o, fn_o = match_overlap(pred_spans, gold)
        overlap_tp += tp_o
        overlap_fp += fp_o
        overlap_fn += fn_o

        # Type-agnostic match
        tp_a, fp_a, fn_a = match_type_agnostic(pred_spans, gold)
        type_agnostic_tp += tp_a
        type_agnostic_fp += fp_a
        type_agnostic_fn += fn_a

        # Dump FP/FN for inspection
        if dump_fp:
            for fp in fp_s:
                fp_dump.append({
                    "example": ex_idx,
                    "type": "FP",
                    "label": fp[2],
                    "start": fp[0],
                    "end": fp[1],
                    "text": text[fp[0]:fp[1]],
                    "full_text": text[:200],
                })
        if dump_fn:
            for fn in fn_s:
                fn_dump.append({
                    "example": ex_idx,
                    "type": "FN",
                    "label": fn[2],
                    "start": fn[0],
                    "end": fn[1],
                    "text": text[fn[0]:fn[1]],
                    "full_text": text[:200],
                })

    # Calculate aggregate scores
    def scores(tp, fp, fn):
        p = tp / (tp + fp) if (tp + fp) else 0.0
        r = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        return p, r, f1

    strict_p, strict_r, strict_f1 = scores(strict_tp, strict_fp, strict_fn)
    overlap_p, overlap_r, overlap_f1 = scores(overlap_tp, overlap_fp, overlap_fn)
    ta_p, ta_r, ta_f1 = scores(type_agnostic_tp, type_agnostic_fp, type_agnostic_fn)

    total_gold = strict_tp + strict_fn
    total_pred = strict_tp + strict_fp

    report = {
        "summary": {
            "examples": len(examples),
            "total_gold_spans": total_gold,
            "total_pred_spans": total_pred,
            "strict":   {"precision": strict_p, "recall": strict_r, "f1": strict_f1,
                         "tp": strict_tp, "fp": strict_fp, "fn": strict_fn},
            "overlap":  {"precision": overlap_p, "recall": overlap_r, "f1": overlap_f1,
                         "tp": overlap_tp, "fp": overlap_fp, "fn": overlap_fn},
            "type_agnostic": {"precision": ta_p, "recall": ta_r, "f1": ta_f1,
                              "tp": type_agnostic_tp, "fp": type_agnostic_fp, "fn": type_agnostic_fn},
        },
        "per_label": {},
    }

    for label, r in sorted(per_label.items()):
        p, r_val, f1 = r.precision, r.recall, r.f1
        n = r.tp + r.fn
        ci_low, ci_high = wilson_ci(f1, n)
        report["per_label"][label] = {
            "precision": round(p, 3),
            "recall": round(r_val, 3),
            "f1": round(f1, 3),
            "tp": r.tp, "fp": r.fp, "fn": r.fn,
            "n_gold": n,
            "f1_ci_95": [round(ci_low, 3), round(ci_high, 3)],
        }

    # Dump FP/FN
    if dump_fp and fp_dump:
        fp_path = Path(eval_set_path).parent / "false_positives.jsonl"
        with open(fp_path, "w", encoding="utf-8") as f:
            for item in fp_dump:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"  False positives dumped to {fp_path} ({len(fp_dump)} items)")
    if dump_fn and fn_dump:
        fn_path = Path(eval_set_path).parent / "false_negatives.jsonl"
        with open(fn_path, "w", encoding="utf-8") as f:
            for item in fn_dump:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"  False negatives dumped to {fn_path} ({len(fn_dump)} items)")

    return report


def print_report(report: dict):
    """Pretty-print the eval report."""
    s = report["summary"]
    print()
    print("=" * 70)
    print("  TIER 1 EVALUATION REPORT")
    print("=" * 70)
    print(f"  Examples:           {s['examples']}")
    print(f"  Total gold spans:   {s['total_gold_spans']}")
    print(f"  Total pred spans:    {s['total_pred_spans']}")
    print()
    print("  ┌──────────────────┬───────────┬──────────┬────────┬───────┐")
    print("  │ Match mode        │ Precision │ Recall   │ F1     │ TP/FP/FN │")
    print("  ├──────────────────┼───────────┼──────────┼────────┼───────┤")
    for mode in ["strict", "overlap", "type_agnostic"]:
        m = s[mode]
        print(f"  │ {mode:<16} │ {m['precision']:.3f}     │ {m['recall']:.3f}    │ {m['f1']:.3f}  │ {m['tp']}/{m['fp']}/{m['fn']} │")
    print("  └──────────────────┴───────────┴──────────┴────────┴───────┘")
    print()
    print("  Per-label breakdown:")
    print("  ┌──────────────────────────┬───────┬────────┬────────┬───────┬────────────┐")
    print("  │ Label                    │ Prec  │ Recall │ F1     │ N     │ F1 95% CI  │")
    print("  ├──────────────────────────┼───────┼────────┼────────┼───────┼────────────┤")
    for label, r in report["per_label"].items():
        ci = r["f1_ci_95"]
        print(f"  │ {label:<24} │ {r['precision']:.3f} │ {r['recall']:.3f}  │ {r['f1']:.3f}  │ {r['n_gold']:>5} │ [{ci[0]:.2f}, {ci[1]:.2f}] │")
    print("  └──────────────────────────┴───────┴────────┴────────┴───────┴────────────┘")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Tier 1 detection engine")
    parser.add_argument("--set", required=True, help="Path to eval set JSONL")
    parser.add_argument("--tier1", action="store_true", help="Run Tier 1 (deterministic)")
    parser.add_argument("--dump-fp", action="store_true", help="Dump false positives to JSONL")
    parser.add_argument("--dump-fn", action="store_true", help="Dump false negatives to JSONL")
    parser.add_argument("--json", action="store_true", help="Output raw JSON report")
    args = parser.parse_args()

    report = run_eval(args.set, dump_fp=args.dump_fp, dump_fn=args.dump_fn)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
