"""Evaluation metrics over the FINAL merged spans (what actually gets masked).

Why character-level leak recall is the headline number: for DLP, a span that is 90% right
still leaks the other 10%. Span-F1 variants are reported too, but leak recall gates releases.

Case format (JSONL):  {"id", "text", "tags": [...], "spans": [{"start","end","label","tier"}]}
  tier = which tier is EXPECTED to own the span (1 deterministic, 2 semantic, 3 NER).
  Scoring a single tier means: only gold spans of that tier are "in scope"; predictions
  are always all counted, so false positives are never hidden.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Protocol, Sequence


class SpanLike(Protocol):
    start: int
    end: int
    label: str


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a proportion k/n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0


@dataclass
class LeakCounts:
    gold_chars: int = 0
    covered_chars: int = 0
    gold_spans: int = 0
    fully_missed: int = 0
    partially_covered: int = 0


@dataclass
class Accumulator:
    strict: Counts = field(default_factory=Counts)
    overlap: Counts = field(default_factory=Counts)      # same label, any overlap
    agnostic: Counts = field(default_factory=Counts)     # any overlap, label ignored
    per_label_strict: dict = field(default_factory=lambda: defaultdict(Counts))
    per_label_leak: dict = field(default_factory=lambda: defaultdict(LeakCounts))
    per_tag_leak: dict = field(default_factory=lambda: defaultdict(LeakCounts))
    benign_cases: int = 0
    benign_cases_with_fp: int = 0
    benign_fp_spans: int = 0
    benign_chars: int = 0
    out_of_scope_gold: int = 0
    latencies_ms: list = field(default_factory=list)
    chars_scanned: int = 0
    misses: list = field(default_factory=list)
    false_positives: list = field(default_factory=list)


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _match_greedy(preds: Sequence[SpanLike], golds: Sequence[dict], need_label: bool) -> tuple[int, int, int]:
    free = list(golds)
    tp = 0
    for p in preds:
        for i, g in enumerate(free):
            if _overlap(p.start, p.end, g["start"], g["end"]) and (not need_label or p.label == g["label"]):
                tp += 1
                free.pop(i)
                break
    return tp, len(preds) - tp, len(free)


def score_case(acc: Accumulator, case: dict, preds: Sequence[SpanLike], tiers: Optional[set[int]]) -> None:
    text = case["text"]
    all_gold = case["spans"]
    gold = [g for g in all_gold if tiers is None or g.get("tier", 1) in tiers]
    acc.out_of_scope_gold += len(all_gold) - len(gold)
    tags = case.get("tags", [])

    # predictions that sit on an out-of-scope gold span are not false positives for this scope
    oos = [g for g in all_gold if g not in gold]
    preds_in = [p for p in preds if not any(_overlap(p.start, p.end, g["start"], g["end"]) for g in oos)]

    # -- strict / overlap / agnostic span metrics
    ps = {(p.start, p.end, p.label) for p in preds_in}
    gs = {(g["start"], g["end"], g["label"]) for g in gold}
    for key in ps & gs:
        acc.strict.tp += 1
        acc.per_label_strict[key[2]].tp += 1
    for key in ps - gs:
        acc.strict.fp += 1
        acc.per_label_strict[key[2]].fp += 1
    for key in gs - ps:
        acc.strict.fn += 1
        acc.per_label_strict[key[2]].fn += 1
    for counts, need in ((acc.overlap, True), (acc.agnostic, False)):
        tp, fp, fn = _match_greedy(preds_in, gold, need)
        counts.tp += tp
        counts.fp += fp
        counts.fn += fn

    # -- character-level leak accounting
    covered = bytearray(len(text))
    for p in preds:
        covered[p.start:p.end] = b"\x01" * (p.end - p.start)
    for g in gold:
        n = g["end"] - g["start"]
        hit = sum(covered[g["start"]:g["end"]])
        for bucket in [acc.per_label_leak[g["label"]], *(acc.per_tag_leak[t] for t in tags)]:
            bucket.gold_chars += n
            bucket.covered_chars += hit
            bucket.gold_spans += 1
            bucket.fully_missed += hit == 0
            bucket.partially_covered += 0 < hit < n
        if hit < n and len(acc.misses) < 500:
            acc.misses.append({"id": case["id"], "label": g["label"], "text": text[g["start"]:g["end"]],
                               "covered_chars": hit, "of": n, "context": text[max(0, g["start"] - 30):g["end"] + 30]})

    # -- benign false positives (cases with NO gold at all)
    if not all_gold:
        acc.benign_cases += 1
        acc.benign_chars += len(text)
        if preds:
            acc.benign_cases_with_fp += 1
            acc.benign_fp_spans += len(preds)
            for p in preds:
                if len(acc.false_positives) < 500:
                    acc.false_positives.append({"id": case["id"], "label": p.label, "text": text[p.start:p.end]})
    acc.chars_scanned += len(text)


def evaluate(cases: Iterable[dict], detect: Callable[[str], Sequence[SpanLike]],
             tiers: Optional[set[int]] = None) -> Accumulator:
    acc = Accumulator()
    for case in cases:
        t0 = time.perf_counter()
        preds = detect(case["text"])
        acc.latencies_ms.append((time.perf_counter() - t0) * 1e3)
        score_case(acc, case, preds, tiers)
    return acc


def report(acc: Accumulator) -> dict:
    def mode(c: Counts) -> dict:
        p, r, f = _prf(c.tp, c.fp, c.fn)
        return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "tp": c.tp, "fp": c.fp, "fn": c.fn}

    def leak(b: LeakCounts) -> dict:
        lo, hi = wilson(b.covered_chars, b.gold_chars)
        return {"char_recall": round(b.covered_chars / b.gold_chars, 4) if b.gold_chars else None,
                "ci95": [round(lo, 3), round(hi, 3)], "spans": b.gold_spans,
                "fully_missed": b.fully_missed, "partial": b.partially_covered}

    total = LeakCounts()
    for b in acc.per_label_leak.values():
        for f in ("gold_chars", "covered_chars", "gold_spans", "fully_missed", "partially_covered"):
            setattr(total, f, getattr(total, f) + getattr(b, f))
    lat = sorted(acc.latencies_ms)
    pct = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 3) if lat else None
    per_label = {}
    for label in sorted(set(acc.per_label_strict) | set(acc.per_label_leak)):
        c = acc.per_label_strict[label]
        p, r, f = _prf(c.tp, c.fp, c.fn)
        per_label[label] = {"strict_p": round(p, 3), "strict_r": round(r, 3), "strict_f1": round(f, 3),
                            "fp": c.fp, **leak(acc.per_label_leak[label])}
    return {
        "leak_recall_overall": leak(total),
        "strict": mode(acc.strict), "overlap": mode(acc.overlap), "type_agnostic": mode(acc.agnostic),
        "benign": {"cases": acc.benign_cases, "cases_with_fp": acc.benign_cases_with_fp,
                   "fp_spans": acc.benign_fp_spans,
                   "fp_case_rate": round(acc.benign_cases_with_fp / acc.benign_cases, 4) if acc.benign_cases else None},
        "out_of_scope_gold_spans": acc.out_of_scope_gold,
        "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": round(lat[-1], 3) if lat else None,
                       "ms_per_kb": round(sum(lat) / (acc.chars_scanned / 1000), 3) if acc.chars_scanned else None},
        "per_label": per_label,
        "per_tag_leak_recall": {t: leak(b) for t, b in sorted(acc.per_tag_leak.items())},
    }
