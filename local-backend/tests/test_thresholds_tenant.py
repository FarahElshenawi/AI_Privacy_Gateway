"""Bake-off thresholds wired into the merge step; cloud tenant config applied live to Tier 1."""
from __future__ import annotations

import json

import pytest

from app import cloud_sync
from app.thresholds import ThresholdsError, load_min_scores, parse_min_scores
from dlp_core.merge import MergeEngine
from dlp_core.span import Evidence, Span


def _sp(label, score, ev=Evidence.MODEL):
    return Span(0, 5, label, score, "t", None, False) if ev is Evidence.MODEL else \
        Span(0, 5, label, score, "t", True, False)


def test_parse_accepts_flat_and_report_shapes_and_ignores_critical_labels():
    assert parse_min_scores({"person": 0.4})[0] == {"PERSON": 0.4}
    scores, ignored = parse_min_scores({"min_scores": {"PERSON": 0.4, "API_KEY": 0.9}})
    assert scores == {"PERSON": 0.4} and ignored == ["API_KEY"]


@pytest.mark.parametrize("bad", [[], {"PERSON": 1.5}, {"PERSON": "x"}, {"PERSON": True}, {"min_scores": 3}])
def test_parse_rejects_bad_input(bad):
    with pytest.raises(ThresholdsError):
        parse_min_scores(bad)


def test_load_from_file_and_fail_closed_on_problems(tmp_path, monkeypatch):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"min_scores": {"PERSON": 0.35, "ORGANIZATION": 0.5}}))
    assert load_min_scores(str(f)) == {"PERSON": 0.35, "ORGANIZATION": 0.5}
    assert load_min_scores("") == {}
    with pytest.raises(ThresholdsError):
        load_min_scores(str(tmp_path / "missing.json"))
    f.write_text("{not json")
    with pytest.raises(ThresholdsError):
        load_min_scores(str(f))


def test_merge_engine_floor_drops_weak_model_spans_only():
    m = MergeEngine(min_scores={"PERSON": 0.6})
    text = "Alice lives here"
    assert m.merge([Span(0, 5, "PERSON", 0.5, "t2")], text) == []                 # below floor: dropped
    assert len(m.merge([Span(0, 5, "PERSON", 0.7, "t2")], text)) == 1
    m.set_min_scores({})
    assert len(m.merge([Span(0, 5, "PERSON", 0.5, "t2")], text)) == 1             # floors can be swapped live


def test_engine_reads_the_file_at_startup(tmp_path, monkeypatch):
    import importlib
    import app.pipeline.engine as eng
    f = tmp_path / "t.json"
    f.write_text('{"PERSON": 0.99}')
    monkeypatch.setenv("DLP_MIN_SCORES_FILE", str(f))
    try:
        importlib.reload(eng)
        assert eng._pipeline._merge._min == {"PERSON": 0.99}
    finally:
        monkeypatch.delenv("DLP_MIN_SCORES_FILE")
        importlib.reload(eng)


@pytest.fixture
def clean_tenant():
    from app.pipeline import engine
    yield engine
    cloud_sync._tenant_version = None
    engine.apply_tenant_config([], [])


def test_tenant_deny_terms_and_domains_apply_live(clean_tenant):
    engine = clean_tenant
    text = "Status of Project Falcon on wiki.corp.acme.com today"
    before = {text[m.start:m.end] for m in engine.detect(text).merged}
    assert "Project Falcon" not in before and "wiki.corp.acme.com" not in before
    assert cloud_sync.apply_tenant_config({"deny_terms": ["Project Falcon"],
                                           "tenant_domains": ["corp.acme.com"], "version": 1})
    after = {text[m.start:m.end] for m in engine.detect(text).merged}
    assert "Project Falcon" in after and "wiki.corp.acme.com" in after
    assert cloud_sync.apply_tenant_config({"deny_terms": [], "tenant_domains": [], "version": 2})
    gone = {text[m.start:m.end] for m in engine.detect(text).merged}
    assert "Project Falcon" not in gone


def test_same_version_is_not_reapplied_and_malformed_config_is_ignored(clean_tenant):
    assert cloud_sync.apply_tenant_config({"deny_terms": ["abc"], "tenant_domains": [], "version": 5})
    assert not cloud_sync.apply_tenant_config({"deny_terms": ["abc"], "tenant_domains": [], "version": 5})
    for bad in ({"deny_terms": "abc"}, {"deny_terms": ["x"]}, {"tenant_domains": ["bad domain"]},
                {"deny_terms": [1]}):
        assert not cloud_sync.apply_tenant_config(bad)
    assert clean_tenant._tier1.config.deny_terms == ("abc",)      # the bad ones changed nothing


def test_pull_applies_tenant_config_with_the_device_token(monkeypatch, clean_tenant):
    monkeypatch.setattr(cloud_sync, "CLOUD_URL", "https://fake")
    monkeypatch.setattr(cloud_sync, "CLOUD_ENROLL_KEY", "k")
    monkeypatch.setattr(cloud_sync, "_endpoint_token", "T" * 43)
    monkeypatch.setattr(cloud_sync, "_token_loaded", True)
    seen = []

    def fake_get(url, hdr, timeout=5):
        seen.append((url, dict(hdr)))
        return ({"deny_terms": ["Project Falcon"], "tenant_domains": [], "version": 9}
                if url.endswith("/tenant-config") else {})
    monkeypatch.setattr(cloud_sync, "_get_json", fake_get)
    cloud_sync._pull_and_apply_policies()
    assert clean_tenant._tier1.config.deny_terms == ("Project Falcon",)
    assert all(h == {"X-Endpoint-Token": "T" * 43} for _, h in seen) and len(seen) == 2
