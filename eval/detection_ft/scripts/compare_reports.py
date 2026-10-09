#!/usr/bin/env python3
"""Before/after per label.   python eval/detection_ft/scripts/compare_reports.py eval/reports/base_v2 eval/reports/ft_v1 [--view tier2]

Reads report.json (+ overlap.json) written by run_baseline --dump. Flags labels that got worse, including
labels you did NOT train on (catastrophic forgetting shows up here)."""
import argparse
import json
from pathlib import Path


def load(d, view):
    r = json.loads((Path(d) / view / "report.json").read_text(encoding="utf-8"))
    o = Path(d) / view / "overlap.json"
    return r, (json.loads(o.read_text(encoding="utf-8")) if o.exists() else {})


def prec(s):
    d = s["tp"] + s["fp"] + s["mism"] if s else 0
    return s["tp"] / d if d else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--view", default="tier2")
    ap.add_argument("--tol", type=float, default=0.02, help="drop bigger than this is flagged as a regression")
    a = ap.parse_args()
    (rb, ob), (ra, oa) = load(a.before, a.view), load(a.after, a.view)
    fmt = lambda x: "  -  " if x is None else f"{x:.3f}"
    dlt = lambda x, y: "     " if x is None or y is None else f"{y - x:+.3f}"
    print(f"{'label':<24}{'recall before':>14}{'after':>8}{'delta':>8}   {'P(ov) before':>12}{'after':>8}{'delta':>8}   note")
    regress = 0
    for l in sorted(set(rb["per_label"]) | set(ra["per_label"])):
        b, c = rb["per_label"].get(l), ra["per_label"].get(l)
        if not (b and b["spans"]) and not (c and c["spans"]):
            continue
        rbv, rav = b and b["char_recall"], c and c["char_recall"]
        pb, pa = prec(ob.get(l)), prec(oa.get(l))
        note = ""
        if rbv is not None and rav is not None and rav < rbv - a.tol:
            note, regress = "<-- WORSE recall", regress + 1
        elif pb is not None and pa is not None and pa < pb - 0.05:
            note = "<-- more false alarms"
        elif rbv is not None and rav is not None and rav > rbv + a.tol:
            note = "improved"
        print(f"{l:<24}{fmt(rbv):>14}{fmt(rav):>8}{dlt(rbv, rav):>8}   {fmt(pb):>12}{fmt(pa):>8}{dlt(pb, pa):>8}   {note}")
    ob_, oa_ = rb["leak_recall_overall"]["char_recall"], ra["leak_recall_overall"]["char_recall"]
    bb, ba = rb["benign"], ra["benign"]
    print(f"\noverall leak recall  {ob_} -> {oa_}")
    print(f"harmless texts flagged  {bb['cases_with_fp']}/{bb['cases']} -> {ba['cases_with_fp']}/{ba['cases']}")
    print(f"labels with worse recall: {regress}" + ("   (check them before shipping)" if regress else ""))


if __name__ == "__main__":
    main()
