import itertools
import random

import pytest

from dlp_core import (Action, Demasker, Evidence, FernetSealer, InMemoryVault, MaskingError,
                      MergeEngine, MergedSpan, OffsetMasker, Policy, Span, VaultCollisionError)


def S(a, b, label="PERSON", **kw):
    return Span(a, b, label, **kw)


def counter_surrogate():
    c = itertools.count(1)
    return lambda label, real: f"Zed{next(c)}"


@pytest.fixture
def vault():
    return InMemoryVault(FernetSealer())


# ---- Span -------------------------------------------------------------
@pytest.mark.parametrize("kw", [dict(start=-1, end=3), dict(start=3, end=3), dict(start=5, end=2),
                                dict(start=0, end=1, score=1.5), dict(start=0, end=1, score=float("nan")),
                                dict(start=0, end=1, label=" ")])
def test_span_rejects_bad_input(kw):
    base = dict(start=0, end=1, label="X")
    base.update(kw)
    with pytest.raises((ValueError, TypeError)):
        Span(**base)


def test_span_normalises_label_and_evidence():
    assert Span(0, 1, " email ").label == "EMAIL"
    assert Span(0, 1, "X", validated=True).evidence is Evidence.VALIDATED
    assert Span(0, 1, "X", validated=False, context=True).evidence is Evidence.CONTEXT
    assert Span(0, 1, "X").evidence is Evidence.MODEL


# ---- MergeEngine ------------------------------------------------------
def test_partial_overlap_is_unioned_not_dropped():
    out = MergeEngine().merge([S(0, 10, "EMAIL"), S(8, 20, "EMAIL")])
    assert [(m.start, m.end) for m in out] == [(0, 20)]


def test_containment_and_chain_union():
    out = MergeEngine().merge([S(0, 5), S(3, 8), S(7, 12), S(2, 4)])
    assert [(m.start, m.end) for m in out] == [(0, 12)]


def test_touching_spans_stay_separate():
    out = MergeEngine().merge([S(0, 5), S(5, 9)])
    assert len(out) == 2


def test_strictest_action_wins_label_conflict():
    out = MergeEngine().merge([S(0, 10, "PERSON", score=0.99), S(4, 8, "API_KEY", score=0.1)])
    assert out[0].action is Action.REDACT and out[0].label == "API_KEY"
    assert (out[0].start, out[0].end) == (0, 10)


def test_evidence_beats_score_within_same_action():
    out = MergeEngine().merge([
        S(0, 16, "CREDIT_CARD", score=0.99),
        S(0, 16, "BANK_ACCOUNT_NUMBER", score=0.6, validated=True),
        S(0, 16, "CVV", score=0.99, context=True)])
    assert out[0].label == "BANK_ACCOUNT_NUMBER"
    assert set(out[0].labels) == {"CREDIT_CARD", "BANK_ACCOUNT_NUMBER", "CVV"}


def test_context_beats_model_score():
    out = MergeEngine().merge([S(0, 4, "CVV", score=0.5, context=True), S(0, 4, "PASSWORD", score=0.99)])
    assert out[0].label == "CVV"


def test_keep_spans_ignored_and_never_widen():
    p = Policy().with_overrides(URL=Action.KEEP)
    out = MergeEngine(p).merge([S(0, 30, "URL"), S(10, 15, "EMAIL")])
    assert [(m.start, m.end) for m in out] == [(10, 15)]


def test_unknown_label_fails_closed():
    assert MergeEngine().merge([S(0, 3, "NEVER_HEARD_OF_IT")])[0].action is Action.REDACT


def test_policy_default_cannot_be_keep():
    with pytest.raises(ValueError):
        Policy(default=Action.KEEP)


def test_snap_widens_partial_token():
    text = "hi Johnson here"
    out = MergeEngine().merge([S(3, 7)], text)
    assert text[out[0].start:out[0].end] == "Johnson"


def test_out_of_bounds_span_raises():
    with pytest.raises(ValueError):
        MergeEngine().merge([S(0, 99)], "short")


def test_min_score_floor_skips_weak_but_not_validated():
    eng = MergeEngine(min_scores={"PERSON": 0.8, "IBAN": 0.8})
    out = eng.merge([S(0, 3, "PERSON", score=0.5), S(5, 9, "IBAN", score=0.1, validated=True)])
    assert [m.label for m in out] == ["IBAN"]


def test_coverage_invariant_fuzz():
    rng = random.Random(7)
    labels = ["PERSON", "EMAIL", "CVV", "API_KEY", "URL"]
    pol = Policy().with_overrides(URL=Action.KEEP)
    eng = MergeEngine(pol, snap_to_word_boundary=False)
    for _ in range(300):
        n = rng.randint(1, 200)
        spans = []
        for _ in range(rng.randint(0, 25)):
            a = rng.randrange(n)
            b = rng.randint(a + 1, n)
            spans.append(S(a, b, rng.choice(labels), score=rng.random(),
                           validated=rng.choice([True, False, None]), context=rng.random() < .3))
        out = eng.merge(spans, "x" * n)
        covered_in = {i for s in spans if pol.action_for(s.label) is not Action.KEEP for i in range(s.start, s.end)}
        covered_out = [i for m in out for i in range(m.start, m.end)]
        assert len(covered_out) == len(set(covered_out))          # disjoint
        assert covered_in == set(covered_out)                     # exact union
        assert all(a.end <= b.start for a, b in zip(out, out[1:]))  # sorted


# ---- OffsetMasker -----------------------------------------------------
def run(text, spans, vault, sur=None, conv="c1", engine=None):
    merged = (engine or MergeEngine()).merge(spans, text)
    return OffsetMasker(vault, sur).mask(text, merged, conv)


def test_john_vs_johnson_untouched(vault):
    text = "John met Johnson. Johnson left."
    res = run(text, [S(0, 4)], vault, counter_surrogate())
    assert res.masked_text == "Zed1 met Johnson. Johnson left."


def test_no_cascading_replacement(vault):
    # "Bob" is a real value later in the text, so it is rejected as a surrogate for
    # Alice; replacements are never re-scanned, so nothing is masked twice.
    text = "Alice and Bob"
    cyc = itertools.cycle(["Bob", "Carol", "Dave"])
    res = run(text, [S(0, 5), S(10, 13)], vault, lambda l, r: next(cyc))
    assert res.masked_text == "Carol and Dave"


def test_redact_has_no_length_leak_and_no_vault_entry(vault):
    a = run("key sk-1", [S(4, 8, "API_KEY")], vault)
    b = run("key sk-1234567890abcdefghij", [S(4, 27, "API_KEY")], vault)
    assert a.masked_text[4:] == b.masked_text[4:] == "[REDACTED:API_KEY]"
    assert vault.items("c1") == []


def test_faker_without_provider_degrades_to_redact(vault):
    res = run("hi Ann", [S(3, 6)], vault, sur=None)
    assert res.masked_text == "hi [REDACTED:PERSON]"
    assert res.spans[0].action is Action.REDACT


def test_surrogate_exhaustion_degrades_to_redact(vault):
    res = run("hi Ann", [S(3, 6)], vault, sur=lambda l, r: "Ann")
    assert "Ann" not in res.masked_text


def test_same_real_same_fake_across_calls(vault):
    sur = counter_surrogate()
    r1 = run("Ann is here", [S(0, 3)], vault, sur)
    r2 = run("call Ann", [S(5, 8)], vault, sur)
    assert r1.masked_text.split()[0] == r2.masked_text.split()[1]
    assert r2.spans[0].reused_surrogate


def test_roundtrip_and_demask_boundaries(vault):
    sur = counter_surrogate()
    text = "John Smith emailed John."
    res = run(text, [S(0, 10), S(19, 23)], vault, sur)
    assert "John" not in res.masked_text
    dm = Demasker(vault)
    assert dm.restore(res.masked_text, "c1")[0] == text
    # Fake "Zed1" must not rewrite inside "Zed10" or "xZed1".
    assert dm.restore("Zed10 xZed1 Zed1.", "c1")[0] == "Zed10 xZed1 John Smith."


def test_demask_longest_first_single_pass(vault):
    vault.put("c", "PERSON", "Real Long", "Max Fake")
    vault.put("c", "PERSON", "Real", "Max")
    assert Demasker(vault).restore("Max Fake and Max", "c")[0] == "Real Long and Real"


def test_masked_offsets_are_correct(vault):
    res = run("a SECRET b SECRET2 c", [S(2, 8, "PASSWORD"), S(11, 18, "PASSWORD")], vault)
    for i in res.spans:
        assert res.masked_text[i.masked_start:i.masked_end] == "[REDACTED:PASSWORD]"


def test_result_and_vault_repr_do_not_leak(vault):
    res = run("Ann", [S(0, 3)], vault, counter_surrogate())
    assert "Ann" not in repr(res) and "Ann" not in repr(vault)
    assert "Ann" not in str(res.spans)


def test_masker_rejects_overlap_or_oob(vault):
    m = OffsetMasker(vault)
    bad = [MergedSpan(0, 5, "X", Action.REDACT, .5, Evidence.MODEL, (), ()),
           MergedSpan(3, 8, "X", Action.REDACT, .5, Evidence.MODEL, (), ())]
    with pytest.raises(MaskingError):
        m.mask("0123456789", bad, "c")
    with pytest.raises(MaskingError):
        m.mask("012", [MergedSpan(0, 9, "X", Action.REDACT, .5, Evidence.MODEL, (), ())], "c")


def test_empty_inputs(vault):
    assert OffsetMasker(vault).mask("", [], "c").masked_text == ""
    assert run("nothing", [], vault).masked_text == "nothing"


def test_unicode_offsets(vault):
    text = "Привет José García!"
    res = run(text, [S(7, 18)], vault, counter_surrogate())
    assert res.masked_text == "Привет Zed1!"
    assert Demasker(vault).restore(res.masked_text, "c1")[0] == text


# ---- Vault ------------------------------------------------------------
def test_vault_bijection_both_directions(vault):
    vault.put("c", "PERSON", "Ann", "Zed")
    vault.put("c", "PERSON", "Ann", "Zed")  # idempotent
    with pytest.raises(VaultCollisionError):
        vault.put("c", "PERSON", "Bea", "Zed")
    with pytest.raises(VaultCollisionError):
        vault.put("c", "PERSON", "Ann", "Other")


def test_vault_conversations_isolated_and_ttl():
    t = [0.0]
    v = InMemoryVault(FernetSealer(), ttl_seconds=10, clock=lambda: t[0])
    v.put("a", "PERSON", "Ann", "Zed")
    assert v.lookup_fake("b", "PERSON", "Ann") is None
    t[0] = 11
    assert v.lookup_fake("a", "PERSON", "Ann") is None and v.items("a") == []
    v.put("a", "PERSON", "Ann", "Zed2")  # slot is reusable after expiry
    assert v.lookup_fake("a", "PERSON", "Ann") == "Zed2"
