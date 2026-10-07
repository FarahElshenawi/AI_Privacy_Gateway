

import pytest as _pytest
from dlp_core.tier1 import Tier1Engine as _E


@_pytest.mark.parametrize("t,expect", [
    ("mailto:a@b.com", ["a@b.com"]), ("email:john.doe@example.com", ["john.doe@example.com"]),
    ("see https://x.com/users/a@b.com/profile", ["a@b.com"]), ("?email=a@b.com&x=1", ["a@b.com"]),
])
def test_email_after_colon_slash_or_equals_is_detected(t, expect):
    got = [t[s.start:s.end] for s in _E().scan(t) if s.label == "EMAIL"]
    assert got == expect


@_pytest.mark.parametrize("t", ["postgresql://user:IJyQdYDNBuwI@db.internal:27017/mydb",
                                "https://admin@host.example.com/x", "ftp://u:p4ss@files.example.org/"])
def test_url_userinfo_is_not_an_email(t):
    assert "EMAIL" not in {s.label for s in _E().scan(t)}
