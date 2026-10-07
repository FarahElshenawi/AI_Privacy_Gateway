import base64
import random

import pytest

from dlp_core import Action, Demasker, Evidence, FernetSealer, InMemoryVault, MergeEngine, OffsetMasker, Span
from dlp_core.tier1 import PatternRecognizer, Tier1Config, Tier1Engine
from dlp_core.tier1 import patterns as P
from dlp_core.tier1 import validators as V

ENGINE = Tier1Engine()


def found(text, engine=ENGINE):
    return [(s.label, text[s.start:s.end]) for s in engine.scan(text)]


def labels(text, engine=ENGINE):
    return {l for l, _ in found(text, engine)}


# ---- contract -----------------------------------------------------------
def test_every_recognizer_returns_span_with_provenance():
    t = "card 4242424242424242 cvv 123 exp 12/25 mail a@b.com phone: 415-555-1234 ip 8.8.8.8"
    spans = ENGINE.scan(t)
    assert spans and all(isinstance(s, Span) and s.source.startswith("tier1.") for s in spans)


# ---- validators ---------------------------------------------------------
def test_validators_math():
    assert V.luhn_valid("4242424242424242") and not V.luhn_valid("4242424242424243")
    assert V.iban_valid("GB82 WEST 1234 5698 7654 32") and not V.iban_valid("GB82WEST12345698765433")
    assert V.aba_valid("021000021") and not V.aba_valid("123456789")
    assert V.base58check_valid("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert not V.base58check_valid("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")
    assert V.bech32_valid("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
    assert not V.ssn_valid("000-45-6789") and not V.ssn_valid("900-12-3456") and V.ssn_valid("123-45-6789")
    assert V.tax_id_valid("12-3456789") and not V.tax_id_valid("17-3456789")
    assert not V.phone_cued_plausible("192.168.1.1") and not V.phone_cued_plausible("2024-01-15")


# ---- cards, CVV, expiry (boundary safety) -------------------------------
@pytest.mark.parametrize("t,exp", [
    ("my card is 4242424242424242", "4242424242424242"),
    ("my card is 4242 4242 4242 4242", "4242 4242 4242 4242"),
    ("my card is 4242-4242-4242-4242", "4242-4242-4242-4242"),
    ("My Visa card 4111  1111  1111  1111 expires soon.", "4111  1111  1111  1111"),   # double spaces
    ("AMEX 378282246310005", "378282246310005"),
    ("12 4242 4242 4242 4242", "4242 4242 4242 4242"),                                  # stray leading group
])
def test_card_variants(t, exp):
    assert ("CREDIT_CARD", exp) in found(t)


def test_invalid_or_short_numbers_not_cards():
    for t in ["card 4242424242424243", "num 424242424242", "order 1234567890123456", "ts 1700000000000"]:
        assert "CREDIT_CARD" not in labels(t)


def test_cvv_and_expiry_adjacent_and_keyworded():
    r = found("Card 4242424242424242, CVV 123, exp 12/25")
    assert ("CVV", "123") in r and ("CARD_EXPIRY", "12/25") in r
    assert ("CVV", "123") in found("CVV: 123") and ("CARD_EXPIRY", "03/27") in found("Expiry date: 03/27")
    assert ("CARD_EXPIRY", "12/2099") in found("Expiry: 12/2099")


def test_cvv_expiry_never_bare_and_no_card_internal_overlap():
    assert not (labels("version 123 and 12/25 meeting") & {"CVV", "CARD_EXPIRY"})
    assert "CARD_EXPIRY" not in labels("Expiry: 13/25")
    # a CVV can't be carved out of the middle of a longer number
    assert "CVV" not in labels("cvv 12345")
    sp = ENGINE.scan("4242424242424242")
    assert [s.label for s in sp] == ["CREDIT_CARD"]


# ---- bank ---------------------------------------------------------------
def test_iban_valid_spaced_and_invalid():
    assert ("IBAN", "GB82WEST12345698765432") in found("IBAN: GB82WEST12345698765432")
    assert ("IBAN", "DE89 3704 0044 0532 0130 00") in found("pay DE89 3704 0044 0532 0130 00 now")
    bad = [s for s in ENGINE.scan("IBAN: GB82WEST12345698765433") if s.label == "IBAN"]
    assert not any(s.validated is True for s in bad)   # may be demoted, never "validated"
    assert "IBAN" not in labels("GB82WEST12345698765433 with no cue")


def test_aba_needs_cue_and_checksum():
    assert ("ABA_ROUTING", "021000021") in found("Routing: 021000021")
    assert "ABA_ROUTING" not in labels("Routing: 123456789")
    assert "ABA_ROUTING" not in labels("id 021000021 bare")


def test_swift_and_bank_account():
    assert ("SWIFT_BIC", "CHASUS33") in found("SWIFT/BIC: CHASUS33")
    assert "SWIFT_BIC" not in labels("the DEUTDEFF word")      # needs cue
    assert ("BANK_ACCOUNT_NUMBER", "12345678") in found("Account: 12345678")
    assert "BANK_ACCOUNT_NUMBER" not in labels("Account: 111111111")


# ---- crypto -------------------------------------------------------------
def test_crypto_wallets():
    assert ("CRYPTO_WALLET", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") in found("Send BTC to 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert "CRYPTO_WALLET" in labels("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
    assert "CRYPTO_WALLET" in labels("ETH: 0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1")
    assert "CRYPTO_WALLET" not in labels("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")


# ---- gov / health -------------------------------------------------------
def test_ssn_tax_mrn_insurance():
    assert ("US_SSN", "123-45-6789") in found("SSN: 123-45-6789")
    assert ("US_SSN", "123456789") in found("My social security number is 123456789")
    assert not (labels("SSN: 000-45-6789") & {"US_SSN"})
    assert ("TAX_ID", "12-3456789") in found("EIN: 12-3456789")
    assert "MEDICAL_RECORD_NUMBER" in labels("MRN: MRN-123456")
    assert "HEALTH_INSURANCE_ID" in labels("Member ID: POLICY1234567")


# ---- email / phone (cue logic) ------------------------------------------
def test_email_and_url_userinfo_not_email():
    assert ("EMAIL", "sarah+news@example.com") in found("Email: sarah+news@example.com")
    assert "EMAIL" not in labels("visit https://example.com/page")
    r = found("DB: postgresql://user:IJyQdYDNBuwI@db.internal:27017/mydb")
    assert "EMAIL" not in {l for l, _ in r} and "CONNECTION_STRING" in {l for l, _ in r}


@pytest.mark.parametrize("t,exp", [
    ("Phone: +1 (415) 555-1234", "+1 (415) 555-1234"),
    ("tel: 415-555-1234", "415-555-1234"),
    ("my mobile is +442071838750", "+442071838750"),
    ("Call me at 4155551234", "4155551234"),
    ("whatsapp 0746915640", "0746915640"),
])
def test_phone_cued_is_context_evidence(t, exp):
    sp = [s for s in ENGINE.scan(t) if s.label == "PHONE_NUMBER"]
    assert [t[s.start:s.end] for s in sp] == [exp]
    assert sp[0].evidence is Evidence.CONTEXT


def test_phone_uncued_left_to_tier2_unless_enabled():
    assert "PHONE_NUMBER" not in labels("ref +14155551234 and 415-555-1234")
    on = Tier1Engine(Tier1Config(emit_uncued_phones=True))
    sp = [s for s in on.scan("ref +14155551234") if s.label == "PHONE_NUMBER"]
    assert sp and sp[0].evidence is Evidence.MODEL


def test_phone_cue_does_not_flag_ip_or_date():
    assert "PHONE_NUMBER" not in labels("call 192.168.1.1 or contact 2024-01-15")


# ---- network ------------------------------------------------------------
def test_ip_and_internal_infra():
    assert "IP_ADDRESS" in labels("server: 192.168.1.42") and "IP_ADDRESS" in labels("IPv6: 2001:db8::1")
    assert not labels("localhost 127.0.0.1 and 0.0.0.0 and 255.255.255.255")
    assert "IP_ADDRESS" not in labels("version 1.2.3")
    assert ("INTERNAL_URL", "http://jenkins.internal:8080") in found("Internal: http://jenkins.internal:8080")
    assert "INTERNAL_HOSTNAME" in labels("Host: db-01.cluster.local")
    assert "INTERNAL_HOSTNAME" not in labels("Host: dev.corp.acme.com")          # needs tenant config
    assert "INTERNAL_URL" not in labels("see https://example.com/docs")
    tenant = Tier1Engine(Tier1Config(tenant_domains=("corp.acme.com",)))
    assert "INTERNAL_URL" in labels("https://gitlab.corp.acme.com/repo", tenant)
    assert "INTERNAL_HOSTNAME" in labels("Host: dev.corp.acme.com", tenant)


# ---- secrets ------------------------------------------------------------
def _jwt():
    b = lambda d: base64.urlsafe_b64encode(d).rstrip(b"=").decode()
    return f'{b(b"{" + b""""alg":"HS256","typ":"JWT"}""")}.{b(b"""{"sub":"1234567890"}""")}.sig_abcdefghijklmnop'


def test_api_keys_signatures():
    for t in ["AWS_KEY=AKIAIOSFODNN7EXAMPLE", "OPENAI_API_KEY=sk-proj-abcdef1234567890abcdefghijklmnopqrstuvwxyz",
              "GITHUB_TOKEN=ghp_ABCDEF1234567890abcdef1234567890ABCD", "stripe: sk_live_abcdef1234567890abcdef",
              "google: AIzaodJFCrnl2edlBDdz1C5Jau2RJtBRnlWmTSH", "slack xoxb-1234567890-abcdef"]:
        assert "API_KEY" in labels(t), t
    assert "API_KEY" not in labels("the quick brown fox sk-ateboard")


def test_jwt_bearer_basic_oauth_labels():
    j = _jwt()
    r = ENGINE.scan(f"Token: {j}")
    assert [s.label for s in r] == ["AUTH_TOKEN"] and r[0].validated is True      # not mislabelled API_KEY
    assert "AUTH_TOKEN" in labels("Bearer abcdef1234567890ABCDEF")
    assert "AUTH_TOKEN" in labels("Authorization: Basic " + base64.b64encode(b"admin:password123").decode())
    assert "AUTH_TOKEN" not in labels("Token: aaa.bbb.ccc")


def test_private_key_pem_full_and_truncated():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0d3e9g7Jh6nM2p5q8R4vWxLc1uYbZ0fHg7sV\n-----END RSA PRIVATE KEY-----"
    r = found(pem)
    assert ("PRIVATE_KEY", pem) in r
    trunc = pem.rsplit("\n", 1)[0] + "\n"
    assert "PRIVATE_KEY" in labels(trunc)
    # merged output has exactly one region over the key
    m = MergeEngine().merge(ENGINE.scan(pem), pem)
    assert len(m) == 1 and (m[0].start, m[0].end) == (0, len(pem))


def test_connection_passwords_cloud_recovery():
    assert "CONNECTION_STRING" in labels("DB: postgresql://user:secret@db.example.com:5432/mydb")
    assert "CONNECTION_STRING" in labels("DB: Server=my.server.com;Database=mydb;User Id=admin;Password=s3cret;")
    assert "CONNECTION_STRING" not in labels("docs: https://example.com/a")
    assert ("PASSWORD", "hunter2secure") in found("Password: hunter2secure")
    for t in ["password = x", "password = your_password_here", "password = ***", "PASSWORD must contain uppercase."]:
        assert "PASSWORD" not in labels(t), t
    k, s = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert ("CLOUD_SECRET", s) in found(f"aws_secret_access_key = {s}")
    assert ("CLOUD_SECRET", s) in found(f"key {k} and then {s}")
    rc = [t for l, t in found("Recovery codes: 1234-5678, 9abc-def0") if l == "RECOVERY_CODE"]
    assert rc == ["1234-5678", "9abc-def0"]


def test_deny_terms():
    e = Tier1Engine(Tier1Config(deny_terms=("Project Falcon", "acme")))
    assert [t for l, t in found("Project Falcon uses ACME, not acmetools", e) if l == "DENY_TERM"] == ["Project Falcon", "ACME"]


# ---- normalisation / offsets -------------------------------------------
def test_obfuscation_is_normalised_and_offsets_map_back():
    t = "pay 4242\u200b 4242\u200c 4242 4242 now"
    r = found(t)
    assert ("CREDIT_CARD", "4242\u200b 4242\u200c 4242 4242") in r
    fw = "card \uff14\uff12\uff14\uff12\uff14\uff12\uff14\uff12\uff14\uff12\uff14\uff12\uff14\uff12\uff14\uff12"
    assert "CREDIT_CARD" in labels(fw)
    t2 = "Ünïcode héré: 4242424242424242"
    s = [x for x in ENGINE.scan(t2) if x.label == "CREDIT_CARD"][0]
    assert t2[s.start:s.end] == "4242424242424242"


# ---- false-positive traps ------------------------------------------------
@pytest.mark.parametrize("t", [
    "The quick brown fox jumps over the lazy dog.", "Order #12345 has been shipped.",
    "Version 2.0.1 was released yesterday.", "Pi is approximately 3.14159265358979323846.",
    "The hash is abc123def456 for this commit.", "Meeting at 3pm on Tuesday in room 412.",
    "Timestamp: 2024-01-15T10:30:00Z for the event.", "Hex color #FF5733 is orange.",
    "Coordinates: 51.5074 N, 0.1278 W.", "Binary: 1010 1010 = 170 in decimal.",
    "Your tracking number is 1Z999AA10123456784.", "ISBN-13: 978-0-13-468599-1 is the text.",
])
def test_benign_text_is_clean(t):
    assert found(t) == [], found(t)


# ---- extensibility -------------------------------------------------------
def test_new_pattern_validator_combo_is_one_object():
    import re
    ticket = PatternRecognizer("ticket", ("TICKET_ID",), re.compile(r"(?<![A-Z0-9])ACME-\d{4,6}(?!\d)"),
                               score=0.9)
    e = Tier1Engine(extra_recognizers=[ticket])
    assert ("TICKET_ID", "ACME-12345") in found("see ACME-12345", e)


# ---- robustness ---------------------------------------------------------
def test_scan_is_linear_on_adversarial_input():
    import time
    t0 = time.perf_counter()
    for t in ["1 " * 20000, "a" * 100000, "4242 " * 5000, "-" * 50000, "@" * 20000, "password=" * 3000]:
        ENGINE.scan(t)
    assert time.perf_counter() - t0 < 8


def test_max_len_and_empty():
    assert ENGINE.scan("") == []
    with pytest.raises(ValueError):
        Tier1Engine(Tier1Config(max_text_len=10)).scan("x" * 11)


def test_fuzz_never_crashes_and_offsets_valid():
    rng = random.Random(3)
    alpha = list("0123456789 -:/=.@abcXYZ\u200b\n+()") + ["4242", "cvv", "exp", "Bearer ", "eyJ", "password", "AKIA"]
    for _ in range(300):
        t = "".join(rng.choice(alpha) for _ in range(rng.randint(0, 120)))
        for s in ENGINE.scan(t):
            assert 0 <= s.start < s.end <= len(t)


# ---- full path: Tier 1 -> Merge -> OffsetMasker -> Demasker -------------
def test_end_to_end_pipeline():
    text = ("Hi, my card 4242 4242 4242 4242 cvv 123 exp 12/25, call +14155551234, "
            "mail jane@acme.com, key AKIAIOSFODNN7EXAMPLE, host 10.0.0.5")
    spans = ENGINE.scan(text)
    merged = MergeEngine().merge(spans, text)
    vault = InMemoryVault(FernetSealer())
    n = iter(range(1, 100))
    res = OffsetMasker(vault, lambda l, r: f"Fake{next(n)}x").mask(text, merged, "c")
    for secret in ["4242 4242", "123", "12/25", "4155551234", "jane@acme.com", "AKIAIOSFODNN7EXAMPLE", "10.0.0.5"]:
        assert secret not in res.masked_text, secret
    assert vault.items("c") == [(f, r) for f, r in vault.items("c")]
    restored, cnt = Demasker(vault).restore(res.masked_text, "c")
    assert "jane@acme.com" in restored and "+14155551234" in restored and cnt >= 2
    assert all(m.action in (Action.REDACT, Action.FAKER) for m in merged)
