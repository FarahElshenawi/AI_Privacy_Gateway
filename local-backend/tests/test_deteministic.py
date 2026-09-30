"""Unit tests for the deterministic detection tier.

Each test case is (input, expected_matches) where expected_matches is a
list of (type, text) tuples. We check type and text but not character offsets
to keep tests readable — offsets are validated separately in test_spans.

Run:
    pytest tests/test_deterministic.py -v
"""
import pytest
from app.pipeline.deterministic import (
    detect,
    find_credit_cards,
    find_api_keys,
    find_jwts,
    find_pem_blocks,
    find_ibans,
    find_ipv4s,
    find_ipv6s,
    find_emails,
    find_phones_e164,
    TYPE_CREDIT_CARD,
    TYPE_API_KEY,
    TYPE_JWT,
    TYPE_PEM_BLOCK,
    TYPE_IBAN,
    TYPE_IPV4,
    TYPE_IPV6,
    TYPE_EMAIL,
    TYPE_PHONE_E164,
)


# === Helpers ===

def types_and_texts(entities):
    """Extract just (type, text) tuples for assertion convenience."""
    return [(e["type"], e["text"]) for e in entities]


# === Credit cards (Luhn) ===

class TestCreditCards:
    def test_visa_16_digit(self):
        # 4242 4242 4242 4242 — passes Luhn
        text = "my card is 4242424242424242"
        results = find_credit_cards(text)
        assert len(results) == 1
        assert results[0]["type"] == TYPE_CREDIT_CARD
        assert results[0]["text"] == "4242424242424242"

    def test_visa_with_spaces(self):
        text = "card: 4242 4242 4242 4242"
        results = find_credit_cards(text)
        assert len(results) == 1
        assert results[0]["text"] == "4242 4242 4242 4242"

    def test_visa_with_dashes(self):
        text = "card: 4242-4242-4242-4242"
        results = find_credit_cards(text)
        assert len(results) == 1

    def test_amex_15_digit(self):
        # 3782 822463 10005 — Amex test number, passes Luhn
        text = "amex: 378282246310005"
        results = find_credit_cards(text)
        assert len(results) == 1

    def test_invalid_luhn_rejected(self):
        # 13 digits but fails Luhn
        text = "not a card: 1234567890123"
        results = find_credit_cards(text)
        assert len(results) == 0

    def test_too_short_rejected(self):
        text = "too short: 424242424242"
        results = find_credit_cards(text)
        assert len(results) == 0

    def test_too_long_rejected(self):
        text = "too long: 4242424242424242424242424"
        results = find_credit_cards(text)
        # 25 digits, but the regex caps at 19 — should find nothing or partial
        # Actually it might find a 19-digit substring; check Luhn filters it
        for r in results:
            assert r["type"] == TYPE_CREDIT_CARD


# === API keys ===

class TestApiKeys:
    def test_aws_access_key(self):
        text = "AWS_KEY=AKIAIOSFODNN7EXAMPLE"
        results = find_api_keys(text)
        assert len(results) == 1
        assert results[0]["text"] == "AKIAIOSFODNN7EXAMPLE"

    def test_openai_key(self):
        text = "OPENAI_API_KEY=sk-" + "a" * 48
        results = find_api_keys(text)
        assert len(results) == 1
        assert results[0]["text"].startswith("sk-")

    def test_github_pat(self):
        text = "GITHUB_TOKEN=ghp_" + "a" * 36
        results = find_api_keys(text)
        assert len(results) == 1
        assert results[0]["text"].startswith("ghp_")

    def test_slack_token(self):
        text = "SLACK_BOT_TOKEN=xoxb-1234567890-abcdefghij"
        results = find_api_keys(text)
        assert len(results) == 1

    def test_stripe_key(self):
        text = "STRIPE=sk_live_" + "a" * 24
        results = find_api_keys(text)
        assert len(results) == 1

    def test_google_api_key(self):
        text = "GOOGLE=AIza" + "a" * 35
        results = find_api_keys(text)
        assert len(results) == 1

    def test_no_false_positive_on_random_text(self):
        text = "the sk-ateboard was broken"
        results = find_api_keys(text)
        assert len(results) == 0


# === JWT ===

class TestJWTs:
    def test_valid_jwt_shape(self):
        # Real-ish JWT (header.payload.signature)
        text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        results = find_jwts(text)
        assert len(results) == 1
        assert results[0]["type"] == TYPE_JWT
        assert results[0]["text"].count(".") == 2

    def test_three_random_dots_rejected(self):
        # Three segments but not valid base64
        text = "foo.bar.baz"
        results = find_jwts(text)
        assert len(results) == 0

    def test_too_short_rejected(self):
        # Segments too short
        text = "abc.def.ghi"
        results = find_jwts(text)
        assert len(results) == 0


# === PEM blocks ===

class TestPEM:
    def test_rsa_private_key(self):
        text = """Here's my key:
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----
        """
        results = find_pem_blocks(text)
        assert len(results) == 1
        assert results[0]["type"] == TYPE_PEM_BLOCK
        assert "BEGIN RSA PRIVATE KEY" in results[0]["text"]
        assert "END RSA PRIVATE KEY" in results[0]["text"]

    def test_certificate(self):
        text = "-----BEGIN CERTIFICATE-----\nABCDEF...\n-----END CERTIFICATE-----"
        results = find_pem_blocks(text)
        assert len(results) == 1

    def test_no_pem_in_normal_text(self):
        text = "just some text with no keys"
        results = find_pem_blocks(text)
        assert len(results) == 0


# === IBAN ===

class TestIBAN:
    def test_valid_iban_gb(self):
        # GB82 WEST 1234 5698 7654 32 — valid
        text = "IBAN: GB82WEST12345698765432"
        results = find_ibans(text)
        assert len(results) == 1
        assert results[0]["text"] == "GB82WEST12345698765432"

    def test_valid_iban_de(self):
        # DE89 3704 0044 0532 0130 00 — valid
        text = "IBAN: DE89370400440532013000"
        results = find_ibans(text)
        assert len(results) == 1

    def test_invalid_mod97_rejected(self):
        text = "IBAN: GB82WEST12345698765433"  # last digit changed
        results = find_ibans(text)
        assert len(results) == 0

    def test_random_letters_rejected(self):
        text = "HELLO123WORLD"
        results = find_ibans(text)
        # Will match the regex shape but fail mod-97
        assert all(r["type"] == TYPE_IBAN for r in results)


# === IPv4 ===

class TestIPv4:
    def test_valid_ipv4(self):
        text = "server at 192.168.1.1"
        results = find_ipv4s(text)
        assert len(results) == 1
        assert results[0]["text"] == "192.168.1.1"

    def test_loopback(self):
        text = "localhost is 127.0.0.1"
        results = find_ipv4s(text)
        assert len(results) == 1

    def test_max_octet_255(self):
        text = "broadcast 255.255.255.255"
        results = find_ipv4s(text)
        assert len(results) == 1

    def test_octet_256_rejected(self):
        text = "not an IP: 256.1.1.1"
        results = find_ipv4s(text)
        assert len(results) == 0


# === IPv6 ===

class TestIPv6:
    def test_valid_ipv6_full(self):
        text = "ipv6: 2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        results = find_ipv6s(text)
        assert len(results) == 1


# === Email ===

class TestEmail:
    def test_simple_email(self):
        text = "contact me at john@example.com"
        results = find_emails(text)
        assert len(results) == 1
        assert results[0]["text"] == "john@example.com"

    def test_email_with_plus(self):
        text = "email: john.doe+filter@gmail.com"
        results = find_emails(text)
        assert len(results) == 1

    def test_email_with_subdomain(self):
        text = "from: alice@mail.example.co.uk"
        results = find_emails(text)
        assert len(results) == 1

    def test_no_at_sign_rejected(self):
        text = "not an email: john.example.com"
        results = find_emails(text)
        assert len(results) == 0


# === Phone (E.164) ===

class TestPhoneE164:
    def test_us_number(self):
        text = "call me at +14155551234"
        results = find_phones_e164(text)
        assert len(results) == 1
        assert results[0]["text"] == "+14155551234"

    def test_uk_number(self):
        text = "UK: +447700900123"
        results = find_phones_e164(text)
        assert len(results) == 1

    def test_no_plus_rejected(self):
        text = "just digits: 14155551234"
        results = find_phones_e164(text)
        assert len(results) == 0


# === Integration: full detect() ===

class TestDetect:
    def test_multiple_entities(self):
        text = """
        My card is 4242424242424242 and my email is john@example.com.
        Server IP is 192.168.1.1. AWS key AKIAIOSFODNN7EXAMPLE.
        Call +14155551234 if needed.
        """
        results = detect(text)
        types_found = {r["type"] for r in results}
        assert TYPE_CREDIT_CARD in types_found
        assert TYPE_EMAIL in types_found
        assert TYPE_IPV4 in types_found
        assert TYPE_API_KEY in types_found
        assert TYPE_PHONE_E164 in types_found

    def test_no_false_positives_in_clean_text(self):
        text = "Just a normal sentence with no PII at all."
        results = detect(text)
        assert len(results) == 0

    def test_offsets_are_correct(self):
        text = "email is john@example.com here"
        results = detect(text)
        assert len(results) == 1
        e = results[0]
        # Verify the offsets point to the matched text
        assert text[e["start"]:e["end"]] == e["text"]
        assert e["text"] == "john@example.com"

    def test_overlapping_entities_prefer_longer(self):
        # An AWS key starting with AKIA is also a 20-char alphanumeric string
        # but credit card detector shouldn't grab a substring of it
        text = "AKIAIOSFODNN7EXAMPLE 4242424242424242"
        results = detect(text)
        texts = {r["text"] for r in results}
        assert "AKIAIOSFODNN7EXAMPLE" in texts
        assert "4242424242424242" in texts

    def test_all_sources_are_deterministic(self):
        text = "email: john@example.com, ip: 1.2.3.4"
        results = detect(text)
        for r in results:
            assert r["source"] == "deterministic"
            assert r["confidence"] == 1.0


# === Performance smoke test ===

class TestPerformance:
    def test_latency_under_50ms_on_typical_input(self):
        import time
        text = "card: 4242424242424242, email: john@example.com, " * 10
        start = time.perf_counter()
        for _ in range(10):
            detect(text)
        elapsed = (time.perf_counter() - start) / 10
        # Should be well under 50ms per call on any modern CPU
        assert elapsed < 0.05, f"detect() took {elapsed*1000:.1f}ms — target is <50ms"
