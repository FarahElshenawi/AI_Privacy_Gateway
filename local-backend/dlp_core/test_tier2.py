"""Tier 2 tests WITHOUT the real model: parsing, offset handling, loading, availability.

These prove our handling of a model's output. They do not prove the model's quality or its exact
output shape: run the bake-off on a machine where the model loads.
"""
import threading
import time

import pytest

from dlp_core import DetectionPipeline, DetectorSpec, MergeEngine, Policy, Status
from dlp_core.policy import DEFAULT_ACTIONS
from dlp_core.tier1 import Tier1Engine
from dlp_core.tier2 import Tier2Config, Tier2Engine, map_label, parse_entities


@pytest.fixture(autouse=True)
def _pretend_gliner2_is_installed(monkeypatch):
    """These tests inject fake models, so they must not depend on gliner2 being installed
    (the idle availability check uses importlib.util.find_spec)."""
    import importlib.util
    real = importlib.util.find_spec

    def find_spec(name, *a, **k):
        return object() if name == "gliner2" else real(name, *a, **k)
    monkeypatch.setattr(importlib.util, "find_spec", find_spec)


# ---- parse_entities ---------------------------------------------------------
def spans_of(text, entities):
    sp, un = parse_entities(text, entities)
    return [(text[s.start:s.end], s.label) for s in sp], un


def test_every_occurrence_is_masked_not_just_the_first():
    t = "John called. Later John left, and John Smith agreed."
    got, un = spans_of(t, {"person": [{"text": "John", "confidence": 0.9}]})
    assert got == [("John", "PERSON")] * 3 and un == 0


def test_word_boundaries_substring_of_longer_word_is_not_matched():
    got, _ = spans_of("John met Johnson and Johnny.", {"person": [{"text": "John", "confidence": 0.9}]})
    assert got == [("John", "PERSON")]


def test_valid_offsets_are_used_and_stripped():
    t = "Hi  Alex Doe  there"
    sp, _ = parse_entities(t, {"full_name": [{"text": "Alex Doe", "start": 3, "end": 13, "confidence": 0.8}]})
    assert [(t[s.start:s.end]) for s in sp] == ["Alex Doe"]


def test_chunk_relative_offsets_that_do_not_match_fall_back_to_search():
    t = "x" * 50 + " Alex Doe lives here"
    sp, un = parse_entities(t, {"person": [{"text": "Alex Doe", "start": 0, "end": 8, "confidence": 0.8}]})   # chunk-relative
    assert [t[s.start:s.end] for s in sp] == ["Alex Doe"] and sp[0].start == 51 and un == 0


def test_case_and_whitespace_fallbacks():
    got, un = spans_of("mail ALEX  DOE now", {"person": [{"text": "alex doe"}]})
    assert got == [("ALEX  DOE", "PERSON")] and un == 0


def test_unlocatable_entities_are_counted_not_silently_dropped():
    got, un = spans_of("nothing here", {"person": [{"text": "Zed Quux", "confidence": 0.9}]})
    assert got == [] and un == 1


def test_shapes_labels_confidence_and_garbage():
    t = "Maria in Cairo"
    sp, _ = parse_entities(t, {"first_name": ["Maria"], "CITY": [{"text": "Cairo", "score": 7}],
                               "weird_label": [{"text": "Maria"}], "person": [None, 5, {"text": ""}]})
    by = {(t[s.start:s.end], s.label): s.score for s in sp}
    assert by[("Maria", "PERSON")] == 0.5 and by[("Cairo", "LOCATION")] == 1.0 and ("Maria", "WEIRD_LABEL") in by
    assert parse_entities(t, None) == ([], 0) and parse_entities(t, "x") == ([], 0)


def test_duplicate_labels_for_same_span_collapse():
    sp, _ = parse_entities("Ann Lee", {"person": [{"text": "Ann Lee"}], "full_name": [{"text": "Ann Lee"}]})
    assert len(sp) == 1


# ---- policy completeness ------------------------------------------------------
def test_every_label_tier2_can_emit_has_an_explicit_policy_entry():
    labels = {map_label(l) for l in Tier2Config().labels}
    missing = labels - set(DEFAULT_ACTIONS)
    assert not missing, f"relying on the fail-closed default: {missing}"


# ---- engine lifecycle -----------------------------------------------------------
class FakeModel:
    def __init__(self, entities=None, delay=0.0):
        self.entities, self.delay, self.calls = entities or {}, delay, 0

    def extract_entities_long(self, text, **kw):
        self.calls += 1
        time.sleep(self.delay)
        assert kw["include_spans"] and kw["entity_types"]
        return {"entities": self.entities}


def engine_with(model):
    class E(Tier2Engine):
        loads = 0

        def _load_pytorch(self):
            E.loads += 1
            time.sleep(0.05)
            self._model = model
    return E(Tier2Config()), E


def test_scan_emits_spans_for_all_occurrences():
    eng, _ = engine_with(FakeModel({"person": [{"text": "Ann", "confidence": 0.9}]}))
    out = eng.scan("Ann met Ann.")
    assert [(s.start, s.end, s.label) for s in out] == [(0, 3, "PERSON"), (8, 11, "PERSON")]


def test_concurrent_first_requests_load_the_model_exactly_once():
    eng, E = engine_with(FakeModel({}))
    ts = [threading.Thread(target=eng.scan, args=("hello",)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert E.loads == 1 and eng.stats()["state"] == "ready"


def test_loading_state_reports_warming_up_and_pipeline_degrades_instead_of_waiting():
    eng, _ = engine_with(FakeModel({}))
    th = threading.Thread(target=eng.warmup)
    th.start()
    time.sleep(0.01)
    assert eng.available is False and eng.unavailable_reason == "warming_up"
    pipe = DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True), DetectorSpec(eng, timeout_s=0.5)])
    r = pipe.run("mail a@b.com")
    t2 = [x for x in r.reports if x.name == "tier2"][0]
    assert t2.status is Status.UNAVAILABLE and t2.error == "warming_up" and r.degraded and not r.blocked
    th.join()
    assert eng.available is True


def test_load_failure_is_contained_reported_and_not_retried_every_request():
    class Broken(Tier2Engine):
        loads = 0

        def _load_pytorch(self):
            Broken.loads += 1
            raise OSError("libtorch_global_deps.so missing")          # torch raises OSError, not ImportError
    eng = Broken()
    pipe = DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True), DetectorSpec(eng)])
    r1, r2 = pipe.run("mail a@b.com"), pipe.run("mail a@b.com")
    assert r1.degraded and not r1.blocked and r1.merged
    assert eng.available is False and eng.unavailable_reason == "load_failed:OSError"
    assert Broken.loads == 1
    assert [x.error for x in r2.reports if x.name == "tier2"] == ["load_failed:OSError"]


def test_disabled_and_missing_package_report_codes():
    assert Tier2Engine(Tier2Config(enabled=False)).unavailable_reason == "disabled"
    eng = Tier2Engine()
    import importlib.util
    if importlib.util.find_spec("gliner2") is None:
        assert eng.unavailable_reason == "gliner2_not_installed"


def test_onnx_is_refused_up_front_instead_of_failing_every_request():
    with pytest.raises(NotImplementedError):
        Tier2Engine(Tier2Config(use_onnx=True, onnx_path="x.onnx"))


def test_end_to_end_names_are_all_masked_through_the_pipeline():
    eng, _ = engine_with(FakeModel({"person": [{"text": "Dana Ruiz", "confidence": 0.9}]}))
    pipe = DetectionPipeline([DetectorSpec(Tier1Engine(), critical=True), DetectorSpec(eng)])
    t = "Dana Ruiz wrote. Then Dana Ruiz called, mail dana@x.org"
    r = pipe.run(t)
    assert [(m.start, m.end) for m in r.merged if m.label == "PERSON"] == [(0, 9), (22, 31)]
    assert not r.degraded
