"""Unit tests for the Tier 1 deterministic detection engine.

Tests the ComplianceValidatorEngine (app.pipeline.tier1) which provides
24 entity types with real mathematical validators (Luhn, mod-97, ABA,
base58check, bech32) and boundary-safe lookarounds.

Run:
    pytest tests/test_deterministic.py -v
"""
import pytest

from app.pipeline.tier1 import ComplianceValidatorEngine


# === Helpers ===

def detect(text):
    """Run the Tier 1 engine and return list of (label, text) tuples."""
    engine = ComplianceValidatorEngine()
    spans = engine.scan_raw(text)
    return [(s.label, text[s.start:s.end]) for s in spans]


def labels(results):
    """Extract just the labels."""
    return [r[0] for r in results]


def texts(results):
    """Extract just the matched texts."""
    return [r[1] for r in results]


# === Credit cards (Luhn + brand) ===

class TestCreditCards:
    def test_visa_16_digit(self):
        results = detect("my card is 4242424242424242")
        assert ("CREDIT_CARD", "4242424242424242") in results

    def test_visa_with_spaces(self):
        results = detect("my card is 4242 4242 4242 4242")
        assert ("CREDIT_CARD", "4242 4242 4242 4242") in results

    def test_visa_with_dashes(self):
        results = detect("my card is 4242-4242-4242-4242")
        assert ("CREDIT_CARD", "4242-4242-4242-4242") in results

    def test_amex_15_digit(self):
        results = detect("card: 378282246310005")
        assert ("CREDIT_CARD", "378282246310005") in results

    def test_invalid_luhn_rejected(self):
        # 4242 4242 4242 4243 — fails Luhn
        results = detect("my card is 4242424242424243")
        assert "CREDIT_CARD" not in labels(results)

    def test_too_short_rejected(self):
        results = detect("my card is 424242424242")
        assert "CREDIT_CARD" not in labels(results)

    def test_cvv_near_card(self):
        results = detect("card 4242424242424242 cvv 123")
        assert "CVV" in labels(results)

    def test_expiry_near_card(self):
        results = detect("card 4242424242424242 exp 12/25")
        assert "CARD_EXPIRY" in labels(results)


# === API keys (prefix + entropy) ===

class TestAPIKeys:
    def test_aws_access_key(self):
        results = detect("AWS_KEY=AKIAIOSFODNN7EXAMPLE")
        assert "API_KEY" in labels(results)

    def test_openai_key(self):
        results = detect("OPENAI_API_KEY=sk-proj-abcdef1234567890abcdefghijklmnopqrstuvwxyz")
        assert "API_KEY" in labels(results)

    def test_github_pat(self):
        results = detect("GITHUB_TOKEN=ghp_ABCDEF1234567890abcdef1234567890ABCD")
        assert "API_KEY" in labels(results)

    def test_stripe_key(self):
        results = detect("stripe: sk_live_abcdef1234567890abcdef")
        assert "API_KEY" in labels(results)

    def test_google_api_key(self):
        # Google API key: AIza + exactly 35 chars
        results = detect("google: AIzaodJFCrnl2edlBDdz1C5Jau2RJtBRnlWmTSH")
        assert "API_KEY" in labels(results)

    def test_no_false_positive_on_random_text(self):
        results = detect("the quick brown fox jumps over the lazy dog")
        assert "API_KEY" not in labels(results)


# === JWT (structure validation) ===

class TestJWT:
    def test_valid_jwt_shape(self):
        # Real JWT structure: header.payload.signature
        results = detect("token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature123abc")
        assert "AUTH_TOKEN" in labels(results)

    def test_invalid_jwt_rejected(self):
        # Not base64 — should not match as JWT
        results = detect("token: aaa.bbb.ccc")
        assert "AUTH_TOKEN" not in labels(results)


# === IBAN (mod-97 + country length) ===

class TestIBAN:
    def test_valid_iban(self):
        results = detect("IBAN: GB82WEST12345698765432")
        assert ("IBAN", "GB82WEST12345698765432") in results

    def test_invalid_iban_rejected(self):
        # Wrong checksum — engine may still flag as low-confidence (validated=False)
        # but it should NOT be validated (no hard evidence)
        engine = ComplianceValidatorEngine()
        spans = engine.scan_raw("IBAN: GB82WEST12345698765433")
        ibans = [s for s in spans if s.label == "IBAN"]
        # May have a low-confidence span, but none should be validated
        assert not any(s.validated is True for s in ibans)


# === Email ===

class TestEmail:
    def test_basic_email(self):
        results = detect("contact: john.doe@example.com")
        assert ("EMAIL", "john.doe@example.com") in results

    def test_plus_addressing(self):
        results = detect("email: sarah+newsletter@example.com")
        assert "EMAIL" in labels(results)

    def test_no_false_positive_on_url(self):
        results = detect("visit https://example.com/page")
        assert "EMAIL" not in labels(results)


# === Phone (E.164) ===

class TestPhone:
    def test_e164_format(self):
        results = detect("call +14155551234")
        assert ("PHONE_NUMBER", "+14155551234") in results


# === IP addresses ===

class TestIPAddresses:
    def test_ipv4(self):
        results = detect("server: 192.168.1.1")
        assert "IP_ADDRESS" in labels(results)

    def test_ipv6(self):
        results = detect("server: 2001:db8::1")
        assert "IP_ADDRESS" in labels(results)

    def test_loopback_ignored(self):
        # 127.0.0.1 is loopback — ignored by default config
        results = detect("localhost: 127.0.0.1")
        assert "IP_ADDRESS" not in labels(results)


# === SSN ===

class TestSSN:
    def test_valid_ssn(self):
        results = detect("SSN: 123-45-6789")
        assert ("US_SSN", "123-45-6789") in results

    def test_invalid_ssn_rejected(self):
        # Area 000 is invalid
        results = detect("SSN: 000-45-6789")
        assert "US_SSN" not in labels(results)


# === Crypto wallets ===

class TestCrypto:
    def test_btc_address(self):
        results = detect("send to 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        assert "CRYPTO_WALLET" in labels(results)


# === Overlap resolution ===

class TestOverlapResolution:
    def test_cvv_inside_card_dropped(self):
        """CVV that overlaps with a validated card number is dropped."""
        results = detect("4242424242424242")
        # The full 16-digit number is a card, not a CVV
        assert "CREDIT_CARD" in labels(results)
        assert "CVV" not in labels(results)

    def test_multiple_entities(self):
        text = "card 4242424242424242, email test@example.com, call +14155551234"
        results = detect(text)
        found_labels = set(labels(results))
        assert "CREDIT_CARD" in found_labels
        assert "EMAIL" in found_labels
        assert "PHONE_NUMBER" in found_labels
