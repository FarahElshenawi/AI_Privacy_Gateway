"""Tier 1: rules for cued seed / recovery phrases (SECRET), the user part of ssh-style user@host (USERNAME),
password cues in other languages, `login: user / secret` pairs, and short values after a seed cue.

Each rule has positive cases and negatives that must stay quiet (plain prose, placeholders, short values)."""
import pytest

from dlp_core.tier1 import Tier1Engine

ENGINE = Tier1Engine()
SEED12 = "orchard bridge ladder comet ribbon tunnel silver garden rocket pigeon walnut saddle"
SEED24 = " ".join(["abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse",
                  "access", "accident", "account", "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act",
                  "action", "actor", "actress", "actual"])


def found(text, label):
    return [text[s.start:s.end] for s in ENGINE.scan(text) if s.label == label]


@pytest.mark.parametrize("t,words", [
    (f"seed phrase: {SEED12}", SEED12.split()),
    (f"My recovery phrase is {SEED12}", SEED12.split()),
    (f"Mnemonic:\n{SEED24}", SEED24.split()),
    (f"backup phrase = {SEED12}", SEED12.split()),
    (f"seed phrase (12 words) -> {SEED12}", SEED12.split()),
    (f"the wallet seed is {SEED12} keep it safe", SEED12.split()),     # may also swallow one trailing plain word
    (f"La frase de recuperaci\u00f3n es {SEED12}", SEED12.split()),
    (f"Meine Wiederherstellungsphrase lautet {SEED12}", SEED12.split()),
    (f"\u0639\u0628\u0627\u0631\u0629 \u0627\u0644\u0627\u0633\u062a\u0631\u062f\u0627\u062f \u0647\u064a {SEED12}", SEED12.split()),
    ("seed words: " + ", ".join(SEED12.split()), SEED12.split()),
])
def test_cued_seed_phrase_is_fully_covered(t, words):
    """Every seed word must sit inside ONE detected span (fail-closed: a trailing plain word may be included)."""
    values = found(t, "SECRET")
    assert any(all(w in v for w in words) for v in values), values


@pytest.mark.parametrize("t", [
    "seed phrase: " + " ".join(SEED12.split()[:11]),                       # only 11 words
    "mnemonic device helps you remember the order of the planets quickly and easily every day",
    "The seed phrase is the secret key that protects the wallet and you should never share it with anyone",
    "seed phrase: the and you your for with that this are was were have",   # stop words = prose
    SEED12,                                                                  # no cue word at all
    "recovery phrase",
    "Our mnemonic " + SEED12,                                                # bare space after the cue
])
def test_seed_phrase_does_not_overfire(t):
    assert found(t, "SECRET") == [], found(t, "SECRET")


@pytest.mark.parametrize("t,users", [
    ("ssh deploy_user@prod-db-01.internal", ["deploy_user"]),
    ("scp report.pdf jsmith@bastion.corp.local:/tmp", ["jsmith"]),
    ("ssh -i ~/.ssh/id_rsa sara.k@10.0.0.5", ["sara.k"]),
    ("rsync -avz ./ mona_f@backup01:/data", ["mona_f"]),
    ("git clone ssh://farah99@build-server/repo.git", ["farah99"]),
    ("scp a.txt u1@h1:/x u2@h2:/y", ["u1", "u2"]),
    ("cd /srv\nssh omar@jump01\nls", ["omar"]),
    ("mosh priya.m91@gw-2", ["priya.m91"]),
    ("sftp ali_h@db01", ["ali_h"]),
])
def test_ssh_user_part_is_detected(t, users):
    assert found(t, "USERNAME") == users, found(t, "USERNAME")


@pytest.mark.parametrize("t", [
    "ssh root@192.168.1.1",                                  # shared service account
    "git clone git@github.com:org/repo.git",                # not an ssh-family command word
    "ssh ubuntu@ec2-1-2-3-4.compute.amazonaws.com",
    "please email me at john@example.com",                  # no command word
    "I wrote to john@example.com about ssh keys",           # the address comes BEFORE the command word
    "ssh keys are great\nmail bob@example.com",             # different line
    "Use ssh to log in",
])
def test_ssh_user_does_not_overfire(t):
    assert found(t, "USERNAME") == [], found(t, "USERNAME")


@pytest.mark.parametrize("t,value", [
    ("la contrase\u00f1a es Sup3rSecret!", "Sup3rSecret!"),
    ("Mot de passe : Zx9!kLm2q", "Zx9!kLm2q"),
    ("Das Passwort lautet Qw3rty!9z", "Qw3rty!9z"),
    ("Passwort: Abcd1234x", "Abcd1234x"),
    ("mdp = Zq8$wLm1", "Zq8$wLm1"),
    ("\u0643\u0644\u0645\u0629 \u0627\u0644\u0633\u0631: abc12345", "abc12345"),
    ("\u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631 \u0647\u064a mXk29LpQzR", "mXk29LpQzR"),
    ("Kennwort ist Hu7!pTq2", "Hu7!pTq2"),
])
def test_password_with_non_english_cue(t, value):
    assert value in found(t, "PASSWORD"), found(t, "PASSWORD")


@pytest.mark.parametrize("t", [
    "La contrase\u00f1a es obligatoria",            # plain word, no secret texture
    "Das Passwort ist falsch",
    "mot de passe : requis",
    "contrase\u00f1a: none",
    "Passwort: ********",
    "la contrase\u00f1a es <tu-contrase\u00f1a>",   # placeholder
    "Wie lautet das Passwort",                      # question, no value
])
def test_non_english_password_cue_does_not_overfire(t):
    assert found(t, "PASSWORD") == [], found(t, "PASSWORD")


@pytest.mark.parametrize("t,value", [
    ("login: alice_w / Sunshine98@", "Sunshine98@"),
    ("creds: farah_h:Winter2026!xk", "Winter2026!xk"),
    ("credentials -> sara.k | Tr0ub4dor&3", "Tr0ub4dor&3"),
    ("login = bob99 / hunter2xyz!", "hunter2xyz!"),
    ("Your login: omar.k@corp / Zk4#mPq9wL\nThanks", "Zk4#mPq9wL"),
])
def test_login_pair_secret_is_detected(t, value):
    assert found(t, "PASSWORD") == [value], found(t, "PASSWORD")


@pytest.mark.parametrize("t", [
    "login: success / failure",
    "login: admin / user",                  # value shorter than 6 characters
    "Login: 2024/05/06",                    # starts with a digit: a date
    "login: 12:30 / 13:45",
    "credentials: none / none",
    "login: alice / <your-password>",
    "creds: john / ********",
    "login: ok / failed again",
])
def test_login_pair_does_not_overfire(t):
    assert found(t, "PASSWORD") == [], found(t, "PASSWORD")


@pytest.mark.parametrize("t,value", [
    ("seed phrase: velvet-tundra-compass", "velvet-tundra-compass"),
    ("recovery phrase is falcon6852", "falcon6852"),
    ("mnemonic: correct-horse-battery-staple", "correct-horse-battery-staple"),
])
def test_short_value_after_seed_cue(t, value):
    assert value in found(t, "SECRET"), found(t, "SECRET")


@pytest.mark.parametrize("t", [
    "seed phrase: n/a", "seed phrase: none", "seed phrase: well-known", "seed phrase: hidden",
    "recovery phrase is required", "mnemonic: abc",
])
def test_short_value_after_seed_cue_does_not_overfire(t):
    assert found(t, "SECRET") == [], found(t, "SECRET")
