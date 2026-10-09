"""CPU benchmark for Tier 2: how long does it take to scan N characters on THIS machine?

    python benchmark_tier2.py [--sizes 500 5000 50000] [--runs 3]

Use the measured seconds-per-1000-characters to set DLP_TIER2_PER_KCHAR_S (keep ~1.5x headroom)
and DLP_TIER2_TIMEOUT_S, and to decide whether /process_file should stay strict on your hardware.
Prints a skip message (exit 0) when the model is not installed.
"""
from __future__ import annotations

import argparse
import statistics
import time

from dlp_core.tier2 import Tier2Config, Tier2Engine

_PARA = ("Alex Morgan moved from 14 Elm Street, Springfield to Berlin in March. "
         "Contact Dana Whitfield about the passport renewal; her colleague Jordan Lee agreed. ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[500, 5_000, 50_000])
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    eng = Tier2Engine(Tier2Config())
    if not eng.available:
        print(f"Tier 2 unavailable ({eng.unavailable_reason}); nothing to benchmark.")
        return 0
    t0 = time.perf_counter()
    eng.warmup()
    print(f"model load + warmup: {time.perf_counter() - t0:.1f}s")
    for n in a.sizes:
        text = (_PARA * (n // len(_PARA) + 1))[:n]
        times = []
        for _ in range(a.runs):
            t = time.perf_counter()
            eng.scan(text)
            times.append(time.perf_counter() - t)
        med = statistics.median(times)
        print(f"{n:>7} chars: median {med:6.2f}s  = {med / (n / 1000):.3f} s per 1000 chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
