"""DetectionPipeline: run every tier in parallel, merge, and say honestly what ran.

    pipe = DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True, timeout_s=0.5),
                              DetectorSpec(UnavailableDetector("tier2", {"PERSON", ...}))])
    result = pipe.run(text)
    result.merged      # disjoint spans to mask
    result.blocked     # a critical detector failed: do NOT send
    result.degraded    # any detector did not complete: send only with a visible warning
    result.uncovered_labels   # labels nobody checked this time (e.g. names while Tier 2 is down)

Fail-closed rules:
  * A detector that raises, times out, returns non-Span objects or out-of-bounds offsets is
    reported (never silently ignored) and its spans are dropped, since its offsets can't be trusted.
  * A failed CRITICAL detector sets `blocked`. A failed non-critical one sets `degraded`.
  * Reports carry only the exception CLASS name, never its message, so user text that a
    library echoes into an error can't leak into logs or API responses.

Limitation (Python threads): a timed-out call cannot be killed. It is abandoned, and the
pipeline refuses to start another call to that detector until the old one finishes, so a hung
model can't pile up threads. Run models that can hang in a subprocess if you need hard kills.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Sequence, runtime_checkable

from .merge import MergedSpan, MergeEngine
from .span import Span


@runtime_checkable
class Detector(Protocol):
    name: str
    labels: frozenset[str]

    def scan(self, text: str) -> Sequence[Span]: ...


class DetectorUnavailable(Exception):
    pass


class UnavailableDetector:
    """Placeholder for a tier whose model isn't loaded. Makes the coverage gap explicit."""
    available = False

    def __init__(self, name: str, labels: Sequence[str]) -> None:
        self.name = name
        self.labels = frozenset(l.upper() for l in labels)

    def scan(self, text: str) -> Sequence[Span]:
        raise DetectorUnavailable(self.name)


class Status(str, Enum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_OUTPUT = "invalid_output"


@dataclass(frozen=True, slots=True)
class DetectorSpec:
    detector: Detector
    critical: bool = False       # failure blocks the request instead of just degrading it
    timeout_s: float = 1.0


@dataclass(frozen=True, slots=True)
class DetectorReport:
    name: str
    status: Status
    labels: frozenset[str]
    critical: bool
    elapsed_ms: float
    span_count: int
    error: Optional[str] = None   # exception class name only


@dataclass(frozen=True, slots=True)
class DetectionResult:
    spans: tuple[Span, ...]            # raw spans from detectors that completed
    merged: tuple[MergedSpan, ...]     # disjoint spans to mask
    reports: tuple[DetectorReport, ...]
    elapsed_ms: float

    @property
    def degraded(self) -> bool:
        return any(r.status is not Status.OK for r in self.reports)

    @property
    def blocked(self) -> bool:
        return any(r.status is not Status.OK and r.critical for r in self.reports)

    @property
    def covered_labels(self) -> frozenset[str]:
        return frozenset(l for r in self.reports if r.status is Status.OK for l in r.labels)

    @property
    def uncovered_labels(self) -> frozenset[str]:
        every = frozenset(l for r in self.reports for l in r.labels)
        return every - self.covered_labels

    def __repr__(self) -> str:   # never expose text
        return (f"DetectionResult(merged={len(self.merged)}, degraded={self.degraded}, "
                f"blocked={self.blocked}, elapsed_ms={self.elapsed_ms:.1f})")


class DetectionPipeline:
    def __init__(self, detectors: Sequence[DetectorSpec], merge: Optional[MergeEngine] = None,
                 *, max_text_len: int = 2_000_000, max_workers: Optional[int] = None) -> None:
        if not detectors:
            raise ValueError("at least one detector is required")
        names = [d.detector.name for d in detectors]
        if len(set(names)) != len(names):
            raise ValueError("detector names must be unique")
        self._specs = tuple(detectors)
        self._merge = merge or MergeEngine()
        self._max_len = max_text_len
        self._pool = ThreadPoolExecutor(max_workers=max_workers or max(8, 2 * len(detectors)),
                                        thread_name_prefix="detect")
        self._abandoned: dict[str, Future] = {}   # timed-out calls that are still running
        self._lock = threading.Lock()

    def set_policy(self, policy) -> None:
        """Hot-swap the merge policy (used by cloud policy sync)."""
        self._merge.set_policy(policy)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def run(self, text: str) -> DetectionResult:
        if len(text) > self._max_len:
            raise ValueError(f"text longer than {self._max_len}; chunk it first")
        t0 = time.perf_counter()
        launched: list[tuple[DetectorSpec, Optional[Future], float]] = []
        reports: dict[str, DetectorReport] = {}

        for spec in self._specs:
            det = spec.detector
            try:
                available = bool(getattr(det, "available", True))
                avail_err = "DetectorUnavailable" if available else (
                    str(getattr(det, "unavailable_reason", None) or "DetectorUnavailable")[:60])
            except Exception as exc:   # noqa: BLE001 - e.g. a broken torch install raising OSError
                available, avail_err = False, type(exc).__name__
            if not available:
                reports[det.name] = self._report(spec, Status.UNAVAILABLE, 0.0, 0, avail_err)
                continue
            with self._lock:
                stuck = self._abandoned.get(det.name)
                if stuck is not None and not stuck.done():
                    reports[det.name] = self._report(spec, Status.TIMEOUT, 0.0, 0, "StillBusy")
                    continue
                self._abandoned.pop(det.name, None)
                fut = self._pool.submit(det.scan, text)
            launched.append((spec, fut, time.perf_counter()))

        spans: list[Span] = []
        for spec, fut, started in launched:
            name = spec.detector.name
            deadline = started + spec.timeout_s
            try:
                out = fut.result(timeout=max(0.0, deadline - time.perf_counter()))
            except FutureTimeout:
                if not fut.cancel():                      # already running: remember it as stuck
                    with self._lock:
                        self._abandoned[name] = fut
                reports[name] = self._report(spec, Status.TIMEOUT, (time.perf_counter() - started) * 1e3, 0, "Timeout")
                continue
            except Exception as exc:   # noqa: BLE001 - any detector failure must be contained
                reports[name] = self._report(spec, Status.FAILED, (time.perf_counter() - started) * 1e3,
                                             0, type(exc).__name__)
                continue
            elapsed = (time.perf_counter() - started) * 1e3
            ok, why = self._validate(out, len(text))
            if not ok:
                reports[name] = self._report(spec, Status.INVALID_OUTPUT, elapsed, 0, why)
                continue
            spans.extend(out)
            reports[name] = self._report(spec, Status.OK, elapsed, len(out), None)

        merged = self._merge.merge(spans, text)
        return DetectionResult(
            spans=tuple(spans),
            merged=tuple(merged),
            reports=tuple(reports[s.detector.name] for s in self._specs),
            elapsed_ms=(time.perf_counter() - t0) * 1e3,
        )

    @staticmethod
    def _validate(out: object, n: int) -> tuple[bool, str]:
        try:
            items = list(out)  # type: ignore[arg-type]
        except TypeError:
            return False, "NotIterable"
        for sp in items:
            if not isinstance(sp, Span):
                return False, "NotASpan"
            if sp.end > n:
                return False, "SpanOutOfBounds"
        return True, ""

    @staticmethod
    def _report(spec: DetectorSpec, status: Status, ms: float, n: int, err: Optional[str]) -> DetectorReport:
        d = spec.detector
        return DetectorReport(d.name, status, frozenset(l.upper() for l in d.labels),
                              spec.critical, ms, n, err)
