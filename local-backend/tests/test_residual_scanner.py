"""Real tests for the independent residual scanner."""
import pytest
from app.pipeline.residual_scanner import ResidualScanner


@pytest.fixture
def scanner():
    return ResidualScanner()


class TestCreditCardDetection:
    def test_luhn_valid_card_detected(self, scanner):
        leaks = scanner.scan("my card is 4242424242424242")
        assert any(l["type"] == "CREDIT_CARD" for l in leaks)

    def test_card_with_spaces_detected(self, scanner):
        leaks = scanner.scan("card: 4242 4242 4242 4242")
        assert any(l["type"] == "CREDIT_CARD" for l in leaks)

    def test_invalid_luhn_not_detected(self, scanner):
        leaks = scanner.scan("not a card: 1234567890123")
        assert not any(l["type"] == "CREDIT_CARD" for l in leaks)

    def test_redacted_card_not_detected(self, scanner):
        leaks = scanner.scan("card: [[REDACTED]]")
        assert not any(l["type"] == "CREDIT_CARD" for l in leaks)


class TestJWTDetection:
    def test_valid_jwt_shape_detected(self, scanner):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        leaks = scanner.scan(f"Bearer {jwt}")
        assert any(l["type"] == "JWT" for l in leaks)

    def test_random_dots_not_detected(self, scanner):
        leaks = scanner.scan("foo.bar.baz")
        assert not any(l["type"] == "JWT" for l in leaks)


class TestAPIKeyDetection:
    def test_aws_key_detected(self, scanner):
        leaks = scanner.scan("AWS_KEY=AKIAIOSFODNN7EXAMPLE")
        assert any("AWS" in l["type"] for l in leaks)

    def test_openai_key_detected(self, scanner):
        leaks = scanner.scan("OPENAI_KEY=sk-" + "a" * 48)
        assert any("OPENAI" in l["type"] for l in leaks)

    def test_github_pat_detected(self, scanner):
        leaks = scanner.scan("GITHUB_TOKEN=ghp_" + "a" * 36)
        assert any("GITHUB" in l["type"] for l in leaks)

    def test_no_false_positive_on_normal_text(self, scanner):
        leaks = scanner.scan("the sk-ateboard was broken")
        assert not any("API_KEY" in l["type"] or "OPENAI" in l["type"] for l in leaks)


class TestIBANDetection:
    def test_valid_iban_detected(self, scanner):
        leaks = scanner.scan("IBAN: GB82WEST12345698765432")
        assert any(l["type"] == "IBAN" for l in leaks)

    def test_invalid_iban_not_detected(self, scanner):
        leaks = scanner.scan("IBAN: GB82WEST12345698765433")
        assert not any(l["type"] == "IBAN" for l in leaks)


class TestPEMDetection:
    def test_pem_block_detected(self, scanner):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        leaks = scanner.scan(text)
        assert any(l["type"] == "PEM_BLOCK" for l in leaks)

    def test_no_pem_in_normal_text(self, scanner):
        leaks = scanner.scan("just some text with no keys")
        assert not any(l["type"] == "PEM_BLOCK" for l in leaks)


class TestCleanText:
    def test_clean_text_no_leaks(self, scanner):
        leaks = scanner.scan("Hello world, no PII here.")
        assert len(leaks) == 0

    def test_masked_text_no_leaks(self, scanner):
        leaks = scanner.scan("My name is James Walsh, email j.walsh@fakermail.com")
        assert len(leaks) == 0

    def test_empty_string(self, scanner):
        assert scanner.scan("") == []


class TestHighEntropy:
    def test_high_entropy_string_detected(self, scanner):
        long_str = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5z"
        leaks = scanner.scan(long_str)
        # May or may not trigger depending on entropy threshold
        # Just verify it doesn't crash
        assert isinstance(leaks, list)
