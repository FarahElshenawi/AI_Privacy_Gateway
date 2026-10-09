"""Tier 2 must fit real inputs: windows, scaled budgets, cooperative deadline, strict files."""
import time

import pytest

from dlp_core.detection import DetectionPipeline, DetectorSpec, Status
from dlp_core.tier1 import Tier1Config, Tier1Engine
from dlp_core.tier2 import Tier2Config, Tier2Engine
from dlp_core.tier2.engine import iter_windows


class FakeModel:
    def __init__(self, delay=0.0):
        self.calls, self.delay = [], delay

    def extract_entities_long(self, text, **kw):
        self.calls.append(len(text))
        time.sleep(self.delay)
        ents = [{"text": "Alice Smith", "confidence": 0.9}] if "Alice Smith" in text else []
        return {"entities": {"person": ents}}


def _engine(model, **cfg):
    e = Tier2Engine(Tier2Config(**cfg))
    e._model, e._state = model, "ready"
    return e


def test_windows_cover_everything_and_cut_at_whitespace():
    text = " ".join(f"word{i}" for i in range(2000))
    wins = list(iter_windows(text, 1000, 100))
    assert wins[0][0] == 0 and all(len(w) <= 1000 for _, w in wins)
    covered = set()
    for off, w in wins:
        covered.update(range(off, off + len(w)))
    assert covered == set(range(len(text)))
    assert not any(w[0:1].isalnum() and off > 0 and text[off - 1].isalnum() for off, w in wins)


def test_long_text_is_scanned_in_windows_and_every_occurrence_is_masked():
    text = ("filler " * 2000) + "Alice Smith " + ("filler " * 2000) + "met Alice Smith again"
    model = FakeModel()
    spans = _engine(model, window_chars=3000, window_overlap=100).scan(text)
    assert len(model.calls) > 3 and max(model.calls) <= 3000
    assert sorted(text[s.start:s.end] for s in spans) == ["Alice Smith", "Alice Smith"]


def test_deadline_stops_the_scan_between_windows():
    text = "filler " * 6000
    model = FakeModel(delay=0.05)
    e = _engine(model, window_chars=1000, window_overlap=50)
    t0 = time.perf_counter()
    with pytest.raises(TimeoutError):
        e.scan(text, deadline=time.perf_counter() + 0.12)
    assert time.perf_counter() - t0 < 1.0 and len(model.calls) < 10
    assert e._infer_lock.acquire(timeout=0.1)            # the model was released, not held to the end
    e._infer_lock.release()


def test_budget_scales_with_size_and_is_capped_per_mode():
    spec = DetectorSpec(object(), timeout_s=3.0, per_kchar_s=0.5, max_s=20.0, file_max_s=300.0)
    assert spec.budget(200) == pytest.approx(3.1)
    assert spec.budget(100_000, "interactive") == 20.0
    assert spec.budget(100_000, "file") == pytest.approx(53.0)
    assert spec.budget(10_000_000, "file") == 300.0
    assert DetectorSpec(object(), timeout_s=1.0).budget(10_000_000) == 1.0       # unchanged default


def test_pipeline_uses_the_scaled_budget_and_reports_timeout_then_recovers():
    text = "filler " * 4000 + "Alice Smith"
    slow = _engine(FakeModel(delay=0.2), window_chars=2000, window_overlap=50)
    pipe = DetectionPipeline([
        DetectorSpec(Tier1Engine(Tier1Config()), critical=True, timeout_s=5.0),
        DetectorSpec(slow, timeout_s=0.3),                     # too small for ~14 windows
    ])
    r = pipe.run(text)
    assert [x.status for x in r.reports] == [Status.OK, Status.TIMEOUT]
    time.sleep(0.5)                                            # the cooperative stop frees the detector
    quick = _engine(FakeModel(), window_chars=2000, window_overlap=50)
    pipe2 = DetectionPipeline([DetectorSpec(Tier1Engine(Tier1Config()), critical=True, timeout_s=5.0),
                               DetectorSpec(quick, timeout_s=0.3, per_kchar_s=0.5)])
    assert pipe2.run(text).reports[1].status is Status.OK


def test_file_strict_defaults_to_true_in_production_config(monkeypatch):
    import importlib
    import app.pipeline.engine as eng
    monkeypatch.delenv("DLP_FILE_STRICT", raising=False)
    try:
        importlib.reload(eng)
        assert eng.FILE_STRICT_DEFAULT is True
    finally:
        monkeypatch.setenv("DLP_FILE_STRICT", "false")
        importlib.reload(eng)
