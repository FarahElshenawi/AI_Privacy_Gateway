import threading
import time

import pytest

from dlp_core import (DetectionPipeline, DetectorSpec, Span, Status, UnavailableDetector)
from dlp_core.tier1 import Tier1Engine


class Fake:
    def __init__(self, name, labels, fn):
        self.name, self.labels, self._fn = name, frozenset(labels), fn

    def scan(self, text):
        return self._fn(text)


def pipe(*specs, **kw):
    return DetectionPipeline(list(specs), **kw)


def test_parallel_union_across_detectors():
    a = Fake("a", {"EMAIL"}, lambda t: [Span(0, 10, "EMAIL", source="a")])
    b = Fake("b", {"EMAIL"}, lambda t: [Span(8, 20, "EMAIL", source="b")])
    r = pipe(DetectorSpec(a), DetectorSpec(b)).run("a " * 15)
    assert [(m.start, m.end) for m in r.merged] == [(0, 20)]
    assert not r.degraded and not r.blocked
    assert {rep.name: rep.status for rep in r.reports} == {"a": Status.OK, "b": Status.OK}


def test_exception_is_contained_and_message_never_leaks():
    def boom(t):
        raise RuntimeError("secret text: " + t)
    r = pipe(DetectorSpec(Fake("m", {"PERSON"}, boom))).run("4242424242424242")
    rep = r.reports[0]
    assert rep.status is Status.FAILED and rep.error == "RuntimeError"
    assert "4242" not in repr(r) and "4242" not in str(rep)
    assert r.degraded and not r.blocked and r.uncovered_labels == {"PERSON"}


def test_critical_failure_blocks():
    def boom(t):
        raise ValueError
    r = pipe(DetectorSpec(Fake("t1", {"EMAIL"}, boom), critical=True)).run("hi")
    assert r.blocked and r.degraded


def test_timeout_and_busy_detector_is_not_resubmitted():
    release = threading.Event()
    calls = []

    def slow(t):
        calls.append(1)
        release.wait(5)
        return []
    p = pipe(DetectorSpec(Fake("slow", {"PERSON"}, slow), timeout_s=0.05))
    r1 = p.run("hello")
    assert r1.reports[0].status is Status.TIMEOUT and r1.reports[0].error == "Timeout"
    r2 = p.run("hello")
    assert r2.reports[0].status is Status.TIMEOUT and r2.reports[0].error == "StillBusy"
    assert len(calls) == 1                      # no thread pile-up
    release.set()
    time.sleep(0.1)
    r3 = p.run("hello")
    assert r3.reports[0].status is Status.OK and len(calls) == 2


def test_slow_detector_does_not_delay_fast_one_beyond_timeout():
    fast = Fake("fast", {"EMAIL"}, lambda t: [Span(0, 3, "EMAIL")])
    slow = Fake("slow", {"PERSON"}, lambda t: time.sleep(1) or [])
    t0 = time.perf_counter()
    r = pipe(DetectorSpec(fast), DetectorSpec(slow, timeout_s=0.1)).run("abcdef")
    assert time.perf_counter() - t0 < 0.5
    assert len(r.merged) == 1 and r.degraded


@pytest.mark.parametrize("bad", [lambda t: [Span(0, 99, "X")], lambda t: [("a", 1)], lambda t: 5])
def test_invalid_output_is_rejected_and_dropped(bad):
    good = Fake("good", {"EMAIL"}, lambda t: [Span(0, 2, "EMAIL")])
    r = pipe(DetectorSpec(Fake("bad", {"X"}, bad)), DetectorSpec(good)).run("hello")
    assert r.reports[0].status is Status.INVALID_OUTPUT
    assert len(r.merged) == 1 and r.merged[0].label == "EMAIL"


def test_unavailable_tier_reports_coverage_gap():
    t2 = UnavailableDetector("tier2", ["PERSON", "ORGANIZATION", "LOCATION"])
    r = pipe(DetectorSpec(Tier1Engine(), critical=True), DetectorSpec(t2)).run("mail a@b.com")
    assert r.degraded and not r.blocked
    assert r.uncovered_labels == {"PERSON", "ORGANIZATION", "LOCATION"}
    assert "EMAIL" in r.covered_labels and len(r.merged) == 1
    assert [x.status for x in r.reports] == [Status.OK, Status.UNAVAILABLE]


def test_validation_and_limits():
    with pytest.raises(ValueError):
        DetectionPipeline([])
    f = Fake("a", {"X"}, lambda t: [])
    with pytest.raises(ValueError):
        pipe(DetectorSpec(f), DetectorSpec(f))
    with pytest.raises(ValueError):
        pipe(DetectorSpec(f), max_text_len=3).run("toolong")


def test_concurrent_runs_are_safe():
    p = pipe(DetectorSpec(Tier1Engine(), critical=True))
    errs = []

    def work(i):
        try:
            r = p.run(f"mail user{i}@example.com")
            assert len(r.merged) == 1 and not r.degraded
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs


def test_availability_probe_that_raises_degrades_instead_of_crashing():
    class BrokenProbe:
        name, labels = "t2", frozenset({"PERSON"})

        @property
        def available(self):
            raise OSError("libtorch_global_deps.so missing")

        def scan(self, text):
            return []
    r = pipe(DetectorSpec(Tier1Engine(), critical=True), DetectorSpec(BrokenProbe())).run("mail a@b.com")
    assert r.degraded and not r.blocked and r.reports[1].status is Status.UNAVAILABLE
    assert r.reports[1].error == "OSError" and len(r.merged) == 1
