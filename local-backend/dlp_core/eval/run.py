"""Run the eval against the real DetectionPipeline.

    python -m dlp_core.eval.run --set eval/holdout_v1.jsonl --tier 1
    python -m dlp_core.eval.run --set eval/holdout_v1.jsonl --tier 1 --dump eval/reports/
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ..detection import DetectionPipeline, DetectorSpec
from ..tier1 import Tier1Engine
from .metrics import evaluate, report


def load(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def frozen_check(path: str) -> str:
    p = Path(path)
    sha = p.with_suffix(".sha256")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    if sha.exists() and sha.read_text().strip() != digest:
        raise SystemExit(f"{p.name} changed since it was frozen; refusing to evaluate on a modified hold-out")
    return digest


def print_report(rep: dict) -> None:
    o = rep["leak_recall_overall"]
    print(f"\nLEAK RECALL (chars, in-scope): {o['char_recall']}  CI95 {o['ci95']}  spans={o['spans']} "
          f"fully_missed={o['fully_missed']} partial={o['partial']}")
    for m in ("strict", "overlap", "type_agnostic"):
        r = rep[m]
        print(f"  {m:<14} P={r['precision']:.3f} R={r['recall']:.3f} F1={r['f1']:.3f}  tp/fp/fn={r['tp']}/{r['fp']}/{r['fn']}")
    b = rep["benign"]
    print(f"  benign: {b['cases_with_fp']}/{b['cases']} cases flagged ({b['fp_spans']} spans)   "
          f"out-of-scope gold spans skipped: {rep['out_of_scope_gold_spans']}")
    lt = rep["latency_ms"]
    print(f"  latency ms: p50={lt['p50']} p95={lt['p95']} max={lt['max']}  ({lt['ms_per_kb']} ms/KB)")
    print("\n  per label (strict P/R/F1 | leak char-recall [n spans, fully missed])")
    for k, v in rep["per_label"].items():
        if v["spans"]:
            print(f"   {k:<24} {v['strict_p']:.2f}/{v['strict_r']:.2f}/{v['strict_f1']:.2f} | "
                  f"{v['char_recall']:.3f} [{v['spans']}, {v['fully_missed']}]  fp={v['fp']}")
        else:
            print(f"   {k:<24} (no gold)  fp={v['fp']}")
    print("\n  leak recall by tag")
    for k, v in rep["per_tag_leak_recall"].items():
        print(f"   {k:<14} {v['char_recall']:.3f} [{v['spans']}, missed {v['fully_missed']}]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--tier", type=int, action="append", help="score only gold spans owned by this tier (repeatable)")
    ap.add_argument("--dump", help="directory for report.json, misses.jsonl, false_positives.jsonl")
    a = ap.parse_args()
    digest = frozen_check(a.set)
    pipe = DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True, timeout_s=30)])
    acc = evaluate(load(a.set), lambda t: pipe.run(t).merged, set(a.tier) if a.tier else None)
    rep = report(acc)
    print(f"set sha256 {digest[:16]}…  scope tiers={a.tier or 'all'}")
    print_report(rep)
    if a.dump:
        d = Path(a.dump); d.mkdir(parents=True, exist_ok=True)
        (d / "report.json").write_text(json.dumps(rep, indent=2))
        (d / "misses.jsonl").write_text("".join(json.dumps(m) + "\n" for m in acc.misses))
        (d / "false_positives.jsonl").write_text("".join(json.dumps(m) + "\n" for m in acc.false_positives))
        print(f"\nreports written to {d}")


if __name__ == "__main__":
    main()
