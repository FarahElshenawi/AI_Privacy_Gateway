"""Baseline v2: how good is each tier, label by label, on OUR data?

Reuses the repo's own scorer (dlp_core/eval/metrics.py). Three views:
  tier1     Tier1Engine alone, scored on gold spans owned by Tier 1
  tier2     Tier2Engine (GLiNER2) alone, scored on gold spans owned by Tier 2
  combined  DetectionPipeline(Tier1 + Tier2) on tier-1 and tier-2 gold = what the user is actually protected from
(Gold spans with tier=3 are realistic context we deliberately do not score either way.)

What is new in v2
  * P(overlap): precision that does not punish boundary differences or nested spans. A prediction is
      TP    if it overlaps a gold span of the SAME label
      nested (ignored) if it sits completely inside a gold span of another label (e.g. a city inside an address)
      MISM  if it only partly overlaps a gold span of a DIFFERENT label (wrong label -> wrong masking action)
      FP    if it overlaps no gold span at all (a real false alarm)
    P(overlap) = TP / (TP + FP + MISM).
  * --t2-add organization phone_number   test extra labels WITHOUT editing tier2/config.py
  * --t2-model PATH                      score a fine-tuned model instead of the default one
  * labels the model predicted that have no gold at all are listed as pure false alarms

    python -m dlp_core.eval.run_baseline --set dlp_core/eval/holdout_v1.jsonl --set eval/semantic_test_v2.jsonl \
        --t2-add organization phone_number --dump eval/reports/base_v2
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from ..detection import DetectionPipeline, DetectorSpec
from ..tier1 import Tier1Engine
from .metrics import evaluate, report
from .run import frozen_check, load

TIER2_LABELS = {"PERSON", "DATE_OF_BIRTH", "ADDRESS", "LOCATION", "USERNAME", "GOVERNMENT_ID",
                "PASSPORT_NUMBER", "DRIVERS_LICENSE_NUMBER", "SENSITIVE_DATE", "SECRET",
                "ORGANIZATION", "ACCOUNT_ID"}


def normalise(cases: list[dict], source: str) -> list[dict]:
    out = []
    for i, c in enumerate(cases):
        c = dict(c)
        c.setdefault("id", f"{Path(source).stem}-{i:05d}")
        c.setdefault("tags", [])
        c["spans"] = [{**s, "tier": s.get("tier", 2 if s["label"] in TIER2_LABELS else 1)} for s in c["spans"]]
        out.append(c)
    return out


def check_local_model(model):
    """If `model` looks like a local folder, verify it really holds a model. Returns (ok, message)."""
    p = Path(model)
    looks_local = p.exists() or p.is_absolute() or "\\" in model or model.startswith((".", "models")) or len(p.parts) > 2
    if not looks_local:
        return True, ""                                   # a Hugging Face id such as fastino/gliner2-...
    has_weights = lambda d: any(f.name.endswith((".safetensors", ".bin")) for f in d.iterdir() if f.is_file())
    if p.is_dir() and (p / "config.json").exists() and has_weights(p):
        return True, ""
    found = []                                            # model folders that DO exist under ./models
    for cfg in sorted(Path("models").rglob("config.json")) if Path("models").is_dir() else []:
        d = cfg.parent
        w = [f for f in d.iterdir() if f.is_file() and f.name.endswith((".safetensors", ".bin"))]
        found.append(f"  {d}   ({'weights ' + format(w[0].stat().st_size / 1e9, '.2f') + ' GB' if w else 'NO weights file'})")
    if p.is_dir():
        problem = "exists but is missing " + " and ".join(
            x for x, ok in (("config.json", (p / "config.json").exists()), ("a weights file (model.safetensors)", has_weights(p))) if not ok)
    else:
        problem = "does not exist"
    msg = f"model folder '{p.resolve()}' {problem}."
    msg += "\n   Model folders found under .\\models:\n" + "\n".join(found) if found else "\n   No model folders found under .\\models."
    msg += "\n   Use --t2-model <one of the folders above>; it must directly contain config.json and model.safetensors."
    return False, msg


def make_tier2(threshold, add_labels=None, model=None):
    """Returns (engine or None, reason). Isolated so tests can swap it out."""
    if model:
        ok, msg = check_local_model(model)
        if not ok:
            return None, msg
    from ..tier2.config import DEFAULT_LABELS, Tier2Config
    from ..tier2.engine import Tier2Engine
    kw = {}
    if threshold is not None:
        kw["threshold"] = threshold
    if model:
        kw["model_name"] = model
    if add_labels:
        kw["labels"] = tuple(DEFAULT_LABELS) + tuple(l.lower() for l in add_labels if l.lower() not in DEFAULT_LABELS)
    eng = Tier2Engine(Tier2Config(**kw) if kw else None)
    if not eng.available:
        return None, "gliner2 is not installed (pip install gliner2 torch transformers peft)"
    try:
        eng.scan("My name is Anna Smith.")      # warm-up: loads the model, surfaces load errors now
    except Exception as e:                      # noqa: BLE001
        # Tier2Engine keeps only the exception CLASS of a failed load (privacy by design), which hides the
        # cause. The warm-up text is a fixed string with no PII, so here we can show the full reason.
        if type(e).__name__ == "DetectorUnavailable":
            try:
                from gliner2 import GLiNER2
                GLiNER2.from_pretrained(eng.config.model_name)
                return None, "engine reported unavailable, but loading the model directly worked (check Tier2Config.enabled/use_onnx)"
            except Exception as e2:             # noqa: BLE001
                return None, f"model load failed -> {type(e2).__name__}: {str(e2)[:600]}"
        return None, f"warm-up scan failed -> {type(e).__name__}: {str(e)[:600]}"
    return eng, ""


class Cached:
    """Wraps a detect function and remembers its predictions so we can score them a second way."""
    def __init__(self, fn):
        self.fn, self.cache = fn, {}

    def __call__(self, text):
        out = list(self.fn(text))
        self.cache[text] = out
        return out


def _ov(p, g) -> bool:
    return p.start < g["end"] and g["start"] < p.end


def overlap_stats(cases, cache, tiers):
    st = defaultdict(lambda: {"tp": 0, "fp": 0, "mism": 0, "nested": 0})
    for c in cases:
        preds = cache.get(c["text"], [])
        gold = [g for g in c["spans"] if tiers is None or g["tier"] in tiers]
        oos = [g for g in c["spans"] if g not in gold]
        for p in preds:
            if any(_ov(p, g) for g in oos):
                continue
            if any(_ov(p, g) and g["label"] == p.label for g in gold):
                st[p.label]["tp"] += 1
            elif any(g["start"] <= p.start and p.end <= g["end"] for g in gold):
                st[p.label]["nested"] += 1
            elif any(_ov(p, g) for g in gold):
                st[p.label]["mism"] += 1
            else:
                st[p.label]["fp"] += 1
    return {k: dict(v) for k, v in st.items()}


def prec(s):
    d = s["tp"] + s["fp"] + s["mism"]
    return (s["tp"] / d) if d else None


def verdict(r, owner, s, a):
    if r is None or not r["spans"]:
        return "-"
    if r["spans"] < a.min_n:
        return f"FEW SAMPLES (n<{a.min_n})"
    if r["char_recall"] is None or r["char_recall"] < a.min_recall:
        return "WEAK recall -> fine-tune candidate" if owner == "tier2" else "WEAK recall -> fix rules (tier1/registry.py)"
    p = prec(s) if s else None
    if p is not None and (s["tp"] + s["fp"] + s["mism"]) >= 10 and p < a.min_precision:
        return f"NOISY (P={p:.2f}) -> hard negatives" if owner == "tier2" else f"NOISY (P={p:.2f}) -> tighten rules"
    return "ok"


def dump(d: Path, rep: dict, acc, ov: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "overlap.json").write_text(json.dumps(ov, indent=2), encoding="utf-8")
    (d / "misses.jsonl").write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in acc.misses), encoding="utf-8")
    (d / "false_positives.jsonl").write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in acc.false_positives), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", required=True, help="eval JSONL (repeatable)")
    ap.add_argument("--dump", help="directory for per-view reports, misses and false positives")
    ap.add_argument("--t2-threshold", type=float, default=None, help="override Tier 2 threshold (config default 0.3)")
    ap.add_argument("--t2-add", nargs="*", default=[], help="extra GLiNER labels to send, e.g. organization phone_number")
    ap.add_argument("--t2-model", default=None, help="path/name of a (fine-tuned) GLiNER2 model")
    ap.add_argument("--tier1-only", action="store_true", help="skip Tier 2 (fast check of rule changes, no model loading)")
    ap.add_argument("--min-recall", type=float, default=0.95, help="leak (char) recall below this = WEAK")
    ap.add_argument("--min-precision", type=float, default=0.70, help="P(overlap) below this (with >=10 predictions) = NOISY")
    ap.add_argument("--min-n", type=int, default=20, help="fewer gold spans than this = verdict not trustworthy")
    a = ap.parse_args()

    cases = []
    for path in a.set:
        digest = frozen_check(path)
        cases += normalise(load(path), path)
        print(f"loaded {path}  sha256 {digest[:12]}")
    n_t1 = sum(s["tier"] == 1 for c in cases for s in c["spans"])
    n_t2 = sum(s["tier"] == 2 for c in cases for s in c["spans"])
    print(f"{len(cases)} cases | gold spans: tier1={n_t1} tier2={n_t2} | "
          f"benign cases (no gold): {sum(not c['spans'] for c in cases)}\n")

    t1 = Tier1Engine()
    if a.tier1_only:
        t2, why = None, "skipped (--tier1-only)"
    else:
        t2, why = make_tier2(a.t2_threshold, a.t2_add, a.t2_model)
    if t2 is None:
        print(f"!! Tier 2 NOT RUN: {why}\n" if not a.tier1_only else "Tier 2 skipped (--tier1-only)\n")
    else:
        print(f"Tier 2 labels sent to the model: {sorted(t2.config.labels) if hasattr(t2, 'config') else '?'}")
        print(f"Tier 2 model: {getattr(getattr(t2, 'config', None), 'model_name', '?')}  threshold: {getattr(getattr(t2, 'config', None), 'threshold', '?')}\n")

    views, ovs = {}, {}

    def run(name, fn, tiers):
        c = Cached(fn)
        acc = evaluate(cases, c, tiers)
        views[name] = (acc, report(acc))
        ovs[name] = overlap_stats(cases, c.cache, tiers)

    run("tier1", t1.scan, {1})
    if t2 is not None:
        run("tier2", t2.scan, {2})
        pipe = DetectionPipeline([DetectorSpec(t1, critical=True, timeout_s=30),
                                  DetectorSpec(t2, critical=False, timeout_s=120)])
        run("combined", lambda t: pipe.run(t).merged, {1, 2})

    for name, (acc, rep) in views.items():
        o, b, s = rep["leak_recall_overall"], rep["benign"], rep["strict"]
        print(f"[{name}] leak recall {o['char_recall']} (CI95 {o['ci95']}, spans={o['spans']}, fully missed={o['fully_missed']}) | "
              f"strict P/R/F1 {s['precision']}/{s['recall']}/{s['f1']} | "
              f"benign cases flagged {b['cases_with_fp']}/{b['cases']} | p50 {rep['latency_ms']['p50']}ms p95 {rep['latency_ms']['p95']}ms")
    print()

    pairs = sorted({(s["label"], s["tier"]) for c in cases for s in c["spans"] if s["tier"] in (1, 2)})
    hdr = (f"{'label':<24}{'owner':<7}{'n':>5}  {'recall':>7}  {'P(overlap)':>10}  {'FP':>4}  {'MISM':>4}  "
           f"{'combined recall':>15}  verdict")
    print(hdr)
    print("-" * (len(hdr) + 34))
    f = lambda x: "  -  " if x is None else f"{x:.3f}"
    rows, weak, noisy = [], [], []
    for l, tr in pairs:
        v = f"tier{tr}"
        if v not in views:
            continue
        r = views[v][1]["per_label"].get(l)
        s = ovs[v].get(l)
        comb = views.get("combined", (None, {"per_label": {}}))[1]["per_label"].get(l)
        rec = r["char_recall"] if r and r["char_recall"] is not None else 2
        rows.append((rec, l, v, r, s, comb))
    for _, l, v, r, s, comb in sorted(rows, key=lambda x: x[0]):
        vd = verdict(r, v, s, a)
        if v == "tier2" and vd.startswith("WEAK"):
            weak.append(l)
        if v == "tier2" and vd.startswith("NOISY"):
            noisy.append(l)
        print(f"{l:<24}{v:<7}{(r['spans'] if r else 0):>5}  {f(r and r['char_recall']):>7}  {f(prec(s) if s else None):>10}  "
              f"{(s['fp'] if s else 0):>4}  {(s['mism'] if s else 0):>4}  {f(comb and comb['char_recall']):>15}  {vd}")
    print("(FP = overlaps no gold at all; MISM = wrong label on a real item; nested spans are ignored)")

    if "tier2" in views:
        gold_labels = {l for l, tr in pairs if tr == 2}
        stray = {l: s for l, s in ovs["tier2"].items() if l not in gold_labels and (s["fp"] + s["mism"] + s["tp"]) > 0}
        if stray:
            print("\nLabels Tier 2 predicted that have NO gold in these files (pure false alarms, or not annotated):")
            for l, s in sorted(stray.items(), key=lambda kv: -(kv[1]["fp"] + kv[1]["mism"] + kv[1]["tp"])):
                print(f"  {l:<24} {s['tp'] + s['fp'] + s['mism']} predictions")
        raw = {t: v for t, v in views["tier2"][1]["per_tag_leak_recall"].items() if t.startswith("raw:")}
        if raw:
            print("\nTier 2 recall by RAW GLiNER label (from tags)")
            for t, v in sorted(raw.items(), key=lambda kv: (kv[1]["char_recall"] is None, kv[1]["char_recall"])):
                print(f"  {t[4:]:<24} recall {v['char_recall']}  [{v['spans']} spans, fully missed {v['fully_missed']}]")

    print("\nDECISION AID")
    if "tier2" not in views:
        print("  Tier 2 was not run, so there is nothing to decide yet.")
    else:
        print(f"  Tier 2 WEAK recall (< {a.min_recall}, n>={a.min_n}): {', '.join(weak) or 'none'}")
        print(f"  Tier 2 NOISY (P(overlap) < {a.min_precision}):       {', '.join(noisy) or 'none'}")
        print("  -> recall problems: more/longer positive examples. Noise problems: more hard negatives.")
        print("  Re-run with --t2-model <fine-tuned path> and compare with compare_reports.py.")
    print("  Tier 1 weak labels are fixed by rules/validators in tier1/registry.py, not by fine-tuning.")

    if a.dump:
        for name, (acc, rep) in views.items():
            dump(Path(a.dump) / name, rep, acc, ovs[name])
        print(f"\nreports written to {a.dump}/<view>/  (look at misses.jsonl and false_positives.jsonl)")


if __name__ == "__main__":
    main()
