import random

from dlp_core.residual_scanner import scan


def test_real_cards_and_keys_found_without_echoing_values():
    f = scan("pay 4242 4242 4242 4242 key AKIAIOSFODNN7EXAMPLE")
    assert {x["type"] for x in f} == {"CREDIT_CARD", "API_KEY"}
    assert all("text" not in x and x["end"] > x["start"] for x in f)
    assert "4242" not in str(f)


def test_random_13_digit_numbers_do_not_fail_close():
    rng = random.Random(1)
    blocked = sum(bool(scan(f"ts={rng.randint(1_600_000_000_000, 1_790_000_000_000)}")) for _ in range(2000))
    assert blocked == 0


def test_long_digit_runs_are_linear():
    import time
    t0 = time.perf_counter()
    scan("1 " * 40000 + "-" * 50000)
    assert time.perf_counter() - t0 < 2
