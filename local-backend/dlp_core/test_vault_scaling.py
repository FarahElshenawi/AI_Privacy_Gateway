"""Vault expiry bookkeeping: correct under staggered expiry, and not quadratic in entry count."""
import time

from dlp_core import FernetSealer, InMemoryVault


def test_staggered_expiry_removes_only_the_expired_entries():
    t = [0.0]
    v = InMemoryVault(FernetSealer(), ttl_seconds=10, clock=lambda: t[0])
    v.put("c", "PERSON", "Ann", "Zed")        # expires at 10
    t[0] = 4
    v.put("c", "PERSON", "Bob", "Yan")        # expires at 14
    t[0] = 11                                  # Ann expired, Bob still live
    assert v.lookup_fake("c", "PERSON", "Ann") is None
    assert v.lookup_fake("c", "PERSON", "Bob") == "Yan"
    assert not v.fake_in_use("c", "Zed") and v.fake_in_use("c", "Yan")
    assert v.items("c") == [("Yan", "Bob")]
    t[0] = 15
    assert v.items("c") == [] and v.lookup_fake("c", "PERSON", "Bob") is None


def test_expiry_bumps_version_so_demasker_cache_invalidates():
    t = [0.0]
    v = InMemoryVault(FernetSealer(), ttl_seconds=10, clock=lambda: t[0])
    v.put("c", "PERSON", "Ann", "Zed")
    before = v.version("c")
    t[0] = 11
    assert v.version("c") != before


def test_expired_slot_is_reusable_in_both_directions():
    t = [0.0]
    v = InMemoryVault(FernetSealer(), ttl_seconds=10, clock=lambda: t[0])
    v.put("c", "PERSON", "Ann", "Zed")
    t[0] = 11
    v.put("c", "PERSON", "Ann", "Other")      # same real, new fake
    v.put("c", "PERSON", "Cat", "Zed")        # old fake, new real
    assert v.lookup_fake("c", "PERSON", "Ann") == "Other"
    assert v.lookup_fake("c", "PERSON", "Cat") == "Zed"


def test_lookups_do_not_scale_with_entry_count():
    """Regression: every lookup used to rescan ALL entries (8,000 entities took ~11 s)."""
    v = InMemoryVault(FernetSealer())
    n = 20_000
    t0 = time.perf_counter()
    for i in range(n):
        v.lookup_fake("c", "EMAIL", f"user{i}@x.org")
        v.put("c", "EMAIL", f"user{i}@x.org", f"fake{i}@y.org")
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, f"{n} lookup+put pairs took {elapsed:.1f}s (quadratic?)"
