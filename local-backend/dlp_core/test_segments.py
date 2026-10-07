import itertools

import pytest

from dlp_core import (DetectionPipeline, DetectorSpec, FernetSealer, InMemoryVault, MergeEngine, OffsetMasker, Span)
from dlp_core.segments import (DetectionBlocked, Edit, Segment, SegmentMasker, apply_edits, apply_edits_to_runs)
from dlp_core.tier1 import Tier1Engine


class Names:
    """Test detector: flags the exact word John / Jane as PERSON (word-bounded)."""
    name, labels = "names", frozenset({"PERSON"})

    def scan(self, text):
        import re
        return [Span(m.start(), m.end(), "PERSON", 0.9, "names") for m in re.finditer(r"\b(?:John|Jane)\b", text)]


def make(max_batch=100_000, extra=()):
    specs = [DetectorSpec(Tier1Engine(), critical=True), *[DetectorSpec(d) for d in extra]]
    pipe = DetectionPipeline(specs)
    vault = InMemoryVault(FernetSealer())
    c = itertools.count(1)
    masker = OffsetMasker(vault, lambda l, r: f"Zed{next(c)}x")
    return SegmentMasker(lambda t: pipe.run(t), masker, max_batch_chars=max_batch), vault


def test_short_values_and_substrings_are_untouched():
    sm, _ = make(extra=[Names()])
    r = sm.mask_segments([Segment("a", "John met Johnson. cvv 123, order 12345, ticket 1234.")], "c")
    assert r.masked["a"] == "Zed1x met Johnson. cvv [REDACTED:CVV], order 12345, ticket 1234."


def test_edits_use_original_coordinates_and_roundtrip():
    sm, _ = make(extra=[Names()])
    text = "Hi John, mail a@b.com now"
    r = sm.mask_segments([Segment("k", text)], "c")
    es = r.edits["k"]
    assert [(e.start, e.end, e.label) for e in es] == [(3, 7, "PERSON"), (14, 21, "EMAIL")]
    assert apply_edits(text, es) == r.masked["k"]


def test_same_person_same_fake_across_segments():
    sm, _ = make(extra=[Names()])
    r = sm.mask_segments([Segment(1, "John here"), Segment(2, "and John there")], "c")
    assert r.masked[1].split()[0] == r.masked[2].split()[1]


def test_card_wrapped_across_two_cells_is_clipped_per_segment():
    sm, _ = make()
    r = sm.mask_segments([Segment("a", "Card: 4242 4242"), Segment("b", "4242 4242 thanks")], "c")
    assert "4242" not in r.masked["a"] and "4242" not in r.masked["b"]
    assert r.masked["b"].endswith("thanks")


def test_long_segment_is_split_at_word_boundaries_and_offsets_stay_correct():
    sm, _ = make(max_batch=200)
    text = ("lorem ipsum " * 40) + "mail me at a@b.com please " + ("dolor sit " * 40)
    r = sm.mask_segments([Segment("x", text)], "c")
    assert "a@b.com" not in r.masked["x"] and r.masked["x"].count("lorem ipsum") == 40
    e = r.edits["x"][0]
    assert text[e.start:e.end] == "a@b.com"


def test_blocked_batch_raises():
    class Boom:
        name, labels = "t1", frozenset({"EMAIL"})

        def scan(self, t):
            raise RuntimeError
    pipe = DetectionPipeline([DetectorSpec(Boom(), critical=True)])
    sm = SegmentMasker(lambda t: pipe.run(t), OffsetMasker(InMemoryVault(FernetSealer())))
    with pytest.raises(DetectionBlocked):
        sm.mask_segments([Segment("a", "hello")], "c")


def test_empty_and_whitespace_segments():
    sm, _ = make()
    r = sm.mask_segments([Segment("a", ""), Segment("b", "  \n "), Segment("c", "x")], "c")
    assert r.masked == {"a": "", "b": "  \n ", "c": "x"} and r.spans_masked == 0


def test_apply_edits_to_runs_preserves_untouched_runs_and_splits_across_runs():
    runs = ["Email: ja", "ne@exam", "ple.com", " bye"]
    text = "".join(runs)
    s = text.index("jane")
    edits = [Edit(s, s + len("jane@example.com"), "[X]", "EMAIL")]
    out = apply_edits_to_runs(runs, edits)
    assert out == ["Email: [X]", "", "", " bye"] and "".join(out) == apply_edits(text, edits)
    assert out[3] is runs[3] or out[3] == runs[3]


def test_apply_edits_to_runs_edit_at_run_boundary_goes_to_next_run():
    out = apply_edits_to_runs(["ab", "cd"], [Edit(2, 4, "ZZ", "X")])
    assert out == ["ab", "ZZ"]
