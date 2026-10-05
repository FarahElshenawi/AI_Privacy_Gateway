"""Generate a realistic evaluation set with 500+ labeled examples.

Each example is {"text": str, "spans": [{"start", "end", "label"}]}.

The generator builds text by concatenating a prefix + PII value + suffix,
then computes the exact character offsets of the PII value in the final
text. This guarantees offsets are always correct.

Distribution (~530 samples):
  - Credit cards:       80  (Visa, MC, Amex, Discover, Diners, JCB, invalid)
  - CVV + expiry:       30  (near cards, with keywords, standalone)
  - Emails:             50  (standard, plus-addressing, subdomains, no-FP)
  - Phone numbers:      45  (E.164, NANP, international, bare 10-digit)
  - API keys:           60  (AWS, GitHub, OpenAI, Stripe, Google, Slack, etc.)
  - JWT / auth tokens:  35  (valid JWT, Bearer, Basic Auth, invalid)
  - IBANs:              40  (various countries, valid + invalid)
  - SWIFT/BIC:          20
  - ABA routing:        20
  - Bank accounts:      20
  - SSNs:               25  (valid, invalid area/group/serial)
  - Tax IDs:            20  (EIN, EU VAT)
  - Medical/health:      20  (MRN, health insurance)
  - IP addresses:       35  (IPv4, IPv6, private, loopback, public)
  - Crypto wallets:     25  (BTC P2PKH, BTC bech32, ETH)
  - Connection strings: 20  (postgres, mysql, mongodb, redis, with password)
  - Passwords:          20  (password=value, pwd: value, placeholder traps)
  - PEM private keys:   15
  - Recovery codes:     15
  - Internal URLs:      15
  - Internal hostnames: 15
  - Negatives (no PII):  60  (version numbers, dates, order IDs, prose)

Run:
    python eval/generate_eval_set.py
    → writes eval/eval_set_v2.jsonl
"""
from __future__ import annotations

import json
import random
import string
from pathlib import Path

random.seed(42)  # reproducible

OUTPUT = Path(__file__).resolve().parent / "eval_set_v2.jsonl"


# ─── Helpers ──────────────────────────────────────────────

def make_example(prefix: str, value: str, suffix: str, label: str) -> dict:
    """Build an example where `value` is the PII, wrapped in prefix/suffix.
    Computes exact character offsets of `value` in the final text."""
    text = prefix + value + suffix
    start = len(prefix)
    end = start + len(value)
    return {"text": text, "spans": [{"start": start, "end": end, "label": label}]}


def make_multi(parts: list[tuple[str, str]]) -> dict:
    """Build an example with multiple PII spans.
    parts: list of (text_segment, label_or_None). When label is None, the
    segment is plain text (no PII). When label is set, a span is recorded."""
    text = ""
    spans = []
    for segment, label in parts:
        start = len(text)
        text += segment
        end = len(text)
        if label:
            spans.append({"start": start, "end": end, "label": label})
    return {"text": text, "spans": spans}


def make_negative(text: str) -> dict:
    """A negative example — no PII, no spans."""
    return {"text": text, "spans": []}


# ─── Luhn-valid card numbers (test numbers, not real) ─────

CARDS = {
    "visa":       ["4242424242424242", "4111111111111111", "4012888888881881",
                   "4222222222222", "4917610000000000"],
    "mastercard": ["5555555555554444", "2221000000000009", "5200000000000007"],
    "amex":       ["378282246310005", "371449635398431", "378734493671000"],
    "discover":   ["6011111111111117", "6011000990139424", "6011601160116611"],
    "diners":     ["30569309025904", "38520000023237"],
    "jcb":        ["3530111333300000", "3566002020360505"],
    "unionpay":   ["6221260000000000", "6222000000000000"],
}
INVALID_CARDS = ["4242424242424243", "4111111111111112", "5555555555554445",
                 "378282246310006", "6011111111111118"]

CARD_SEPARATORS = ["", " ", "-", "  ", "-"]


def fmt_card(num: str) -> str:
    sep = random.choice(CARD_SEPARATORS)
    if not sep:
        return num
    # Group in 4s
    groups = [num[i:i+4] for i in range(0, len(num), 4)]
    return sep.join(groups)


# ─── Email generators ─────────────────────────────────────

EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
                "example.com", "company.co.uk", "university.edu", "proton.me"]
EMAIL_LOCALS = ["john.doe", "sarah_thompson", "user123", "first.last",
                "contact", "admin", "support", "jane+newsletter",
                "test.user", "noreply", "info", "hello.world"]


def gen_email() -> str:
    return f"{random.choice(EMAIL_LOCALS)}@{random.choice(EMAIL_DOMAINS)}"


# ─── Phone generators ─────────────────────────────────────

def gen_e164() -> str:
    cc = random.choice(["1", "44", "33", "49", "81", "86", "91", "55", "61"])
    num = "".join(random.choices(string.digits, k=random.randint(8, 12)))
    return f"+{cc}{num}"


def gen_nanp() -> str:
    area = random.choice(["415", "212", "310", "617", "202", "404", "312", "713"])
    exch = f"{random.choice('23456789')}{random.choices(string.digits, k=2)[0]}{random.choices(string.digits, k=1)[0]}"
    sub = "".join(random.choices(string.digits, k=4))
    sep = random.choice(["-", ".", " "])
    return f"({area}) {exch}{sep}{sub}"


# ─── API key generators ───────────────────────────────────

def gen_aws_key() -> str:
    return "AKIA" + "".join(random.choices(string.ascii_uppercase + string.digits, k=16))


def gen_github_pat() -> str:
    return "ghp_" + "".join(random.choices(string.ascii_letters + string.digits, k=36))


def gen_openai_key() -> str:
    suffix = "".join(random.choices(string.ascii_letters + string.digits + "_-", k=40))
    return f"sk-proj-{suffix}"


def gen_stripe_key() -> str:
    suffix = "".join(random.choices(string.ascii_letters + string.digits, k=24))
    return f"sk_live_{suffix}"


def gen_google_key() -> str:
    return "AIza" + "".join(random.choices(string.ascii_letters + string.digits + "_-", k=35))


def gen_slack_token() -> str:
    return "xoxb-" + "".join(random.choices(string.digits, k=11)) + "-" + \
           "".join(random.choices(string.ascii_letters + string.digits, k=11))


def gen_gitlab_pat() -> str:
    return "glpat-" + "".join(random.choices(string.ascii_letters + string.digits + "_-", k=20))


def gen_sendgrid_key() -> str:
    p1 = "".join(random.choices(string.ascii_letters + string.digits + "_-", k=22))
    p2 = "".join(random.choices(string.ascii_letters + string.digits + "_-", k=43))
    return f"SG.{p1}.{p2}"


def gen_twilio_key() -> str:
    return "SK" + "".join(random.choices(string.hexdigits.lower(), k=32))


def gen_npm_token() -> str:
    return "npm_" + "".join(random.choices(string.ascii_letters + string.digits, k=36))


def gen_hf_token() -> str:
    return "hf_" + "".join(random.choices(string.ascii_letters + string.digits, k=30))


# ─── JWT generator ────────────────────────────────────────

import base64

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def gen_jwt() -> str:
    header = b64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = b64url(b'{"sub":"1234567890","name":"John Doe","iat":1516239022}')
    sig = b64url(bytes(ord(c) for c in (random.choices(string.ascii_letters + string.digits, k=32))))
    return f"{header}.{payload}.{sig}"


# ─── IBAN generators (valid checksums) ────────────────────

IBANS_VALID = [
    "GB82WEST12345698765432", "DE89370400440532013000", "FR1420041010050500013M02606",
    "IT60X0542811101000000123456", "ES9121000418450200051332", "NL91ABNA0417164300",
    "BE68539007547034", "CH9300762011623852957", "AT611904300234573201",
    "IE29AIBK93115212345678", "PT50000201231234567890154",
    "BE62510007547034",  # duplicate length test
]
IBANS_INVALID = ["GB82WEST12345698765433", "DE89370400440532013001", "FR1420041010050500013M02607"]


# ─── SWIFT/BIC ───────────────────────────────────────────

SWIFTS = ["CHASUS33", "DEUTDEFF", "BNPAFRPP", "BARCGB22", "BOFAUS3N",
          "HSBCGB22", "CITIUS33", "INGBNL2A", "RABONL2U", "ABNANL2A"]


# ─── ABA routing (valid checksums) ───────────────────────

ABA_VALID = ["021000021", "026013576", "121000358", "122000661",
             "071000013", "111000025", "031000503", "061000052"]
ABA_INVALID = ["123456789", "000000000", "999999999"]


# ─── SSN ─────────────────────────────────────────────────

SSN_VALID = ["123-45-6789", "456-78-9012", "589-22-3344", "777-12-4321"]
SSN_INVALID_AREA = ["000-45-6789", "666-12-3456", "900-11-2222"]
SSN_INVALID_GROUP = ["123-00-6789"]
SSN_INVALID_SERIAL = ["123-45-0000"]


# ─── Crypto wallets ───────────────────────────────────────

BTC_P2PKH = ["1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
             "1P5ZEDW15TFMR7kpcC6E2vQHuJoF3hYtpe"]
BTC_BECH32 = ["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
              "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
              "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxj2m3k"]
ETH = ["0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb1",
       "0x5a0b54d5dc17e0aadc383d2db43b0a0d3e029c4c",
       "0x314159265dD8dbb310642f98f50C066173C1259b"]


# ─── Connection strings ───────────────────────────────────

CONN_STRINGS = [
    "postgresql://user:secretpass@db.example.com:5432/mydb",
    "mysql://admin:password123@localhost:3306/shop",
    "mongodb://root:hunter2@cluster.mongodb.net:27017/data",
    "redis://:s3cr3t@cache.internal:6379/0",
    "amqp://guest:guest@rabbitmq.local:5672/vhost",
    "mssql://sa:P@ssw0rd@sql.internal:1433/prod",
    "jdbc:postgresql://user:pass@db.host.com:5432/app",
    "Server=my.server.com;Database=mydb;User Id=admin;Password=s3cret;",
]


# ─── PEM private keys ────────────────────────────────────

PEM_RSA = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0d3e9g7Jh6nM2p5q8R4vWxLc1uYbZ0fHg7sV
-----END RSA PRIVATE KEY-----"""

PEM_EC = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIJ5o2qL+5j3j3j3j3j3j3j3j3j3j3j3j3j3j3j3j3j3j3j
-----END EC PRIVATE KEY-----"""

PEM_OPENSSH = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB
-----END OPENSSH PRIVATE KEY-----"""


# ─── Internal infrastructure ──────────────────────────────

INTERNAL_URLS = [
    "http://jenkins.internal:8080/build",
    "https://gitlab.corp.acme.com/repo",
    "http://wiki.intranet.local/page",
    "https://grafana.dev.corp/dashboards",
    "http://nexus.lan:8081/repository",
]

INTERNAL_HOSTNAMES = [
    "jenkins.internal",
    "gitlab.corp.acme.com",
    "wiki.intranet.local",
    "grafana.dev.corp",
    "nexus.lan",
    "db-server.cluster.local",
]


# ─── Prefixes / suffixes (realistic context) ─────────────

PREFIXES = [
    "My ", "The ", "Please use ", "Contact: ", "", "Here is my ", "Update the ",
    "Server config: ", "Database: ", "", "Hi, ", "For payment, ", "", "API key: ",
    "Token: ", "", "Credentials: ", "", "My info: ", "", "Customer data: ",
    "I need to share my ", "", "Payment: ", "Contact me at ", "", "Login: ",
    "Connection string: ", "Database URL: ", "", "Account: ", "Routing: ",
    "IBAN: ", "SWIFT: ", "SSN: ", "My phone is ", "Call me at ", "Email: ",
    "Card: ", "Credit card: ", "My card is ", "", "Server IP: ", "Host: ",
    "Send to ", "BTC address: ", "ETH: ", "Wallet: ", "Password: ", "Secret: ",
    "Bearer ", "Authorization: Basic ", "Private key: ", "Recovery codes: ",
    "Backup codes: ", "EIN: ", "VAT: ", "MRN: ", "Member ID: ",
    "AWS key: ", "GitHub token: ", "OpenAI key: ", "Stripe key: ", "Google key: ",
    "", "Debug: ", "Config: ", "Test data: ", "",
]

SUFFIXES = [
    " for verification.", " please.", "", " thanks!", "", " — is this correct?",
    " and confirm.", "", " ASAP.", "", " for the payment.", "", " thanks in advance.",
    "", " what do you think?", "", " please review.", " for the order.",
    "", " let me know.", "", " any issues?", "", " for my account.",
    "", " can you help?", "", " I need this done.", "", " for the transfer.",
    "", "", " please check.", " for the deployment.", "", " thanks!",
    "", " and update the records.", "", " for billing.", "",
    " please verify.", "", " for the test.", "", " — don't share this.",
    "", " for the database.", "", " for the API.", "", " for the server.",
    "", " and let me know.", "",
]


# ─── Negatives (false-positive traps) ───────────────────

NEGATIVES = [
    "The quick brown fox jumps over the lazy dog.",
    "Meeting at 3pm on Tuesday in conference room B.",
    "Order #12345 has been shipped to your address.",
    "The year is 2024 and the temperature is 25.5 degrees Celsius.",
    "Version 2.0.1 was released yesterday.",
    "Pi is approximately 3.14159265358979323846.",
    "The file size is 1048576 bytes or 1 megabyte.",
    "Random numbers: 12345 67890 54321 98765.",
    "Just a normal sentence without any sensitive information.",
    "The meeting ID is 123-456-789 for the Zoom call.",
    "Your tracking number is 1Z999AA10123456784.",
    "The ISBN is 978-3-16-148410-0 for the textbook.",
    "Flight UA1234 departs at 6:45 AM from gate B12.",
    "The coordinates are 37.7749 N, 122.4194 W.",
    "Total price: $1,234.56 including tax and shipping.",
    "Chapter 12, page 345, paragraph 2 of the manual.",
    "The answer is 42 according to the Hitchhiker's Guide.",
    "File path: /usr/local/bin/python3 on this system.",
    "Environment variable HOME is set to /home/user.",
    "The port is 8080 and the protocol is HTTPS.",
    "My favorite color is blue and I like pizza.",
    "The distance is 42.195 kilometers for a full marathon.",
    "Equation: E = mc^2 where c is the speed of light.",
    "The hash is abc123def456 for this commit.",
    "Build number 2024.01.15-rc1 passed all tests.",
    "Latitude 40.7128, longitude -74.0060 for New York City.",
    "The list contains: apple, banana, cherry, date.",
    "Timestamp: 2024-01-15T10:30:00Z for the event.",
    "User agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64).",
    "The score was 3-2 in favor of the home team.",
    "ISBN-13: 978-0-13-468599-1 is the textbook we need.",
    "Reference number: REF-2024-001-ABC for your records.",
    "The ratio is 16:9 for widescreen video format.",
    "Coordinates: 51.5074 N, 0.1278 W for London.",
    "Approximately 7.8 billion people on Earth.",
    "The speed limit is 65 mph on this highway.",
    "Chapter 7, verse 12 of the book of Genesis.",
    "Product SKU: ABC-1234-XYZ for the catalog.",
    "The zip code 90210 is in Beverly Hills, California.",
    "Temperature: -40 degrees (same in F and C).",
    "The model number is XJ-7200-V3 for the device.",
    "Serial number: SN-2024-001234 for the warranty.",
    "The duration is 3:45 minutes for this song.",
    "Recipe calls for 2 cups of flour and 1 teaspoon of salt.",
    "The pH is 7.0 which is neutral on the scale.",
    "Speed: 299,792,458 meters per second in vacuum.",
    "The angle is 45 degrees from the horizontal.",
    "Atmospheric pressure is 1013.25 hPa at sea level.",
    "Population density: 1,234 people per square kilometer.",
    "The combination is left-right-left, 32-12-28.",
    "Binary: 1010 1010 = 170 in decimal.",
    "Hex color #FF5733 is a shade of orange.",
    "Unicode character U+0041 is the letter A.",
    "Regular expression: ^[a-z]+$ matches lowercase only.",
    "The IP-like 10.0.0 is just a version number, not an address.",
    "This is just a paragraph with some numbers like 42 and 3.14.",
    "The meeting room is on floor 4, room 412.",
    "Document ID: DOC-2024-0001 revision 3.",
    "The checksum is 0xDEADBEEF in hexadecimal notation.",
]


# ─── Main generator ───────────────────────────────────────

def generate_all() -> list[dict]:
    examples = []

    # === Credit cards (80) ===
    for brand, nums in CARDS.items():
        for num in nums:
            prefix = random.choice(PREFIXES)
            suffix = random.choice(SUFFIXES)
            examples.append(make_example(prefix, fmt_card(num), suffix, "CREDIT_CARD"))
            # Also without separator
            if random.random() < 0.4:
                examples.append(make_example(
                    random.choice(PREFIXES), num, random.choice(SUFFIXES), "CREDIT_CARD"
                ))
    # Invalid cards (no spans)
    for num in INVALID_CARDS:
        examples.append(make_negative(f"Card number: {fmt_card(num)} (invalid)"))
    # Extra valid cards with realistic context
    for _ in range(8):
        num = random.choice(CARDS["visa"] + CARDS["mastercard"])
        examples.append(make_example(
            "My Visa card ", fmt_card(num), " expires soon.", "CREDIT_CARD"
        ))

    # === CVV + expiry (30) ===
    cvv_values = ["123", "456", "789", "999", "1234"]
    exp_values = ["12/25", "03/27", "11/26", "01/28", "06/25", "09/26"]
    for _ in range(15):
        cvv = random.choice(cvv_values)
        examples.append(make_example("CVV: ", cvv, random.choice(SUFFIXES), "CVV"))
    for _ in range(15):
        exp = random.choice(exp_values)
        examples.append(make_example("Expiry: ", exp, random.choice(SUFFIXES), "CARD_EXPIRY"))

    # Card + CVV + expiry combined
    for _ in range(5):
        num = fmt_card(random.choice(CARDS["visa"]))
        cvv = random.choice(cvv_values)
        exp = random.choice(exp_values)
        text = f"Card: {num}, CVV: {cvv}, Exp: {exp}"
        spans = []
        for label, val in [("CREDIT_CARD", num), ("CVV", cvv), ("CARD_EXPIRY", exp)]:
            idx = text.index(val)
            spans.append({"start": idx, "end": idx + len(val), "label": label})
        examples.append({"text": text, "spans": spans})

    # === Emails (50) ===
    for _ in range(50):
        email = gen_email()
        prefix = random.choice(["Email: ", "Contact: ", "My email is ", "", "Send to ", "Reply to "])
        suffix = random.choice([" please.", "", " thanks!", " for updates.", ""])
        examples.append(make_example(prefix, email, suffix, "EMAIL"))
    # False-positive traps for email
    examples.append(make_negative("Visit https://example.com/page for more info."))
    examples.append(make_negative("The file is report.pdf in the docs folder."))

    # === Phone numbers (45) ===
    for _ in range(20):
        phone = gen_e164()
        examples.append(make_example("Call ", phone, random.choice(SUFFIXES), "PHONE_NUMBER"))
    for _ in range(15):
        phone = gen_nanp()
        examples.append(make_example("Phone: ", phone, random.choice(SUFFIXES), "PHONE_NUMBER"))
    for _ in range(10):
        # Bare 10-digit with keyword
        phone = "".join(random.choices(string.digits, k=10))
        examples.append(make_example("Phone: ", phone, random.choice(SUFFIXES), "PHONE_NUMBER"))

    # === API keys (60) ===
    key_gens = [
        ("AWS: ", gen_aws_key, "API_KEY"),
        ("GitHub token: ", gen_github_pat, "API_KEY"),
        ("OpenAI key: ", gen_openai_key, "API_KEY"),
        ("Stripe key: ", gen_stripe_key, "API_KEY"),
        ("Google key: ", gen_google_key, "API_KEY"),
        ("Slack token: ", gen_slack_token, "API_KEY"),
        ("GitLab PAT: ", gen_gitlab_pat, "API_KEY"),
        ("SendGrid key: ", gen_sendgrid_key, "API_KEY"),
        ("Twilio key: ", gen_twilio_key, "API_KEY"),
        ("npm token: ", gen_npm_token, "API_KEY"),
        ("HF token: ", gen_hf_token, "API_KEY"),
    ]
    for prefix, gen_fn, label in key_gens:
        for _ in range(5):
            examples.append(make_example(prefix, gen_fn(), random.choice(SUFFIXES), label))

    # === JWT / auth tokens (35) ===
    for _ in range(15):
        jwt = gen_jwt()
        examples.append(make_example("Token: ", jwt, random.choice(SUFFIXES), "AUTH_TOKEN"))
    for _ in range(10):
        token = "".join(random.choices(string.ascii_letters + string.digits + ".~_=-", k=32))
        examples.append(make_example("Bearer ", token, random.choice(SUFFIXES), "AUTH_TOKEN"))
    for _ in range(5):
        cred = base64.b64encode(b"admin:password123").decode()
        examples.append(make_example("Authorization: Basic ", cred, "", "AUTH_TOKEN"))
    for _ in range(5):
        token = "ya29." + "".join(random.choices(string.ascii_letters + string.digits + "_-", k=40))
        examples.append(make_example("Google OAuth: ", token, "", "AUTH_TOKEN"))
    # Invalid JWT (no spans)
    examples.append(make_negative("Token: aaa.bbb.ccc is not valid."))
    examples.append(make_negative("The string abc.def is too short."))

    # === IBANs (40) ===
    for iban in IBANS_VALID:
        examples.append(make_example("IBAN: ", iban, random.choice(SUFFIXES), "IBAN"))
        # Also with spaces
        spaced = iban[:4] + " " + iban[4:8] + " " + iban[8:12] + " " + iban[12:]
        if random.random() < 0.3:
            examples.append(make_example("IBAN: ", spaced, "", "IBAN"))
    for iban in IBANS_INVALID:
        examples.append(make_negative(f"IBAN: {iban} (invalid checksum)"))
    for _ in range(10):
        iban = random.choice(IBANS_VALID)
        examples.append(make_example("Transfer to ", iban, " please.", "IBAN"))

    # === SWIFT/BIC (20) ===
    for swift in SWIFTS:
        examples.append(make_example("SWIFT/BIC: ", swift, random.choice(SUFFIXES), "SWIFT_BIC"))
    for _ in range(10):
        swift = random.choice(SWIFTS)
        examples.append(make_example("Bank code: ", swift, "", "SWIFT_BIC"))

    # === ABA routing (20) ===
    for aba in ABA_VALID:
        examples.append(make_example("Routing number: ", aba, random.choice(SUFFIXES), "ABA_ROUTING"))
    for aba in ABA_INVALID:
        examples.append(make_negative(f"Routing: {aba} (invalid)"))
    for _ in range(5):
        aba = random.choice(ABA_VALID)
        examples.append(make_example("ABA: ", aba, "", "ABA_ROUTING"))

    # === Bank accounts (20) ===
    for _ in range(20):
        acct = "".join(random.choices(string.digits, k=random.randint(8, 14)))
        examples.append(make_example("Account number: ", acct, random.choice(SUFFIXES), "BANK_ACCOUNT_NUMBER"))
    # False-positive traps
    examples.append(make_negative("Account: 111111111 (all same digits)"))
    examples.append(make_negative("Account: 123 (too short)"))

    # === SSNs (25) ===
    for ssn in SSN_VALID:
        examples.append(make_example("SSN: ", ssn, random.choice(SUFFIXES), "US_SSN"))
        # Also bare (no keyword)
        if random.random() < 0.3:
            examples.append(make_example("", ssn, random.choice(SUFFIXES), "US_SSN"))
    for ssn in SSN_INVALID_AREA + SSN_INVALID_GROUP + SSN_INVALID_SERIAL:
        examples.append(make_negative(f"SSN: {ssn} (invalid)"))

    # === Tax IDs (20) ===
    for _ in range(10):
        ein = f"{random.randint(1, 99):02d}-{random.randint(1000000, 9999999)}"
        examples.append(make_example("EIN: ", ein, random.choice(SUFFIXES), "TAX_ID"))
    for country in ["GB", "DE", "FR", "IT", "ES", "NL"]:
        vat = f"{country}{random.randint(10000000, 99999999)}"
        examples.append(make_example("VAT: ", vat, random.choice(SUFFIXES), "TAX_ID"))

    # === Medical / health (20) ===
    for _ in range(10):
        mrn = f"MRN-{random.randint(100000, 999999)}"
        examples.append(make_example("MRN: ", mrn, random.choice(SUFFIXES), "MEDICAL_RECORD_NUMBER"))
    for _ in range(10):
        policy = f"POLICY{random.randint(1000000, 9999999)}"
        examples.append(make_example("Member ID: ", policy, random.choice(SUFFIXES), "HEALTH_INSURANCE_ID"))

    # === IP addresses (35) ===
    for _ in range(10):
        ip = f"{random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
        examples.append(make_example("Server IP: ", ip, random.choice(SUFFIXES), "IP_ADDRESS"))
    for _ in range(8):
        ip = f"192.168.{random.randint(0, 255)}.{random.randint(1, 254)}"
        examples.append(make_example("Internal IP: ", ip, random.choice(SUFFIXES), "IP_ADDRESS"))
    for _ in range(5):
        ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
        examples.append(make_example("Private IP: ", ip, random.choice(SUFFIXES), "IP_ADDRESS"))
    for _ in range(5):
        ip = f"2001:db8::{random.randint(1, 999)}"
        examples.append(make_example("IPv6: ", ip, random.choice(SUFFIXES), "IP_ADDRESS"))
    # False positives (loopback, multicast)
    examples.append(make_negative("Localhost is 127.0.0.1 for development."))
    examples.append(make_negative("The address 0.0.0.0 means all interfaces."))
    examples.append(make_negative("Broadcast 255.255.255.255 is standard."))
    examples.append(make_negative("Multicast 224.0.0.1 is for routing."))

    # === Crypto wallets (25) ===
    for addr in BTC_P2PKH:
        examples.append(make_example("BTC: ", addr, random.choice(SUFFIXES), "CRYPTO_WALLET"))
    for addr in BTC_BECH32:
        examples.append(make_example("Send to ", addr, random.choice(SUFFIXES), "CRYPTO_WALLET"))
    for addr in ETH:
        examples.append(make_example("ETH wallet: ", addr, random.choice(SUFFIXES), "CRYPTO_WALLET"))
    for _ in range(10):
        addr = random.choice(BTC_P2PKH + ETH)
        examples.append(make_example("Wallet: ", addr, "", "CRYPTO_WALLET"))

    # === Connection strings (20) ===
    for cs in CONN_STRINGS:
        examples.append(make_example("DB: ", cs, random.choice(SUFFIXES), "CONNECTION_STRING"))
    for _ in range(12):
        user = random.choice(["admin", "root", "user", "app"])
        pwd = "".join(random.choices(string.ascii_letters + string.digits, k=12))
        host = random.choice(["db.example.com", "localhost", "db.internal", "cluster.net"])
        port = random.choice([5432, 3306, 27017, 6379, 1433])
        db = random.choice(["mydb", "app", "prod", "test"])
        cs = f"postgresql://{user}:{pwd}@{host}:{port}/{db}"
        examples.append(make_example("Connection: ", cs, "", "CONNECTION_STRING"))

    # === Passwords (20) ===
    for _ in range(20):
        pwd = "".join(random.choices(string.ascii_letters + string.digits, k=random.randint(8, 16)))
        prefix = random.choice(["Password: ", "password = ", "pwd: ", "passphrase: ", "PWD = "])
        examples.append(make_example(prefix, pwd, random.choice(SUFFIXES), "PASSWORD"))
    # False-positive traps
    examples.append(make_negative("password = *** (redacted)"))
    examples.append(make_negative("password = your_password_here"))
    examples.append(make_negative("The password field is required."))
    examples.append(make_negative("PASSWORD must contain uppercase."))

    # === PEM private keys (15) ===
    for _ in range(5):
        examples.append(make_example("", PEM_RSA, "", "PRIVATE_KEY"))
    for _ in range(5):
        examples.append(make_example("", PEM_EC, "", "PRIVATE_KEY"))
    for _ in range(5):
        examples.append(make_example("", PEM_OPENSSH, "", "PRIVATE_KEY"))

    # === Recovery codes (15) ===
    for _ in range(15):
        codes = ", ".join([
            f"{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"
            for _ in range(random.randint(3, 6))
        ])
        text = f"Recovery codes: {codes}"
        spans = []
        idx = len("Recovery codes: ")
        for m in __import__("re").finditer(r"\d{4}-\d{4}", codes):
            s = idx + m.start()
            e = idx + m.end()
            spans.append({"start": s, "end": e, "label": "RECOVERY_CODE"})
        examples.append({"text": text, "spans": spans})

    # === Internal URLs (15) ===
    for url in INTERNAL_URLS:
        examples.append(make_example("Internal: ", url, random.choice(SUFFIXES), "INTERNAL_URL"))
    for _ in range(10):
        url = random.choice(INTERNAL_URLS)
        examples.append(make_example("", url, "", "INTERNAL_URL"))

    # === Internal hostnames (15) ===
    for host in INTERNAL_HOSTNAMES:
        examples.append(make_example("Host: ", host, random.choice(SUFFIXES), "INTERNAL_HOSTNAME"))
    for _ in range(9):
        host = random.choice(INTERNAL_HOSTNAMES)
        examples.append(make_example("", host, "", "INTERNAL_HOSTNAME"))

    # === Negatives / false-positive traps (60) ===
    for neg in NEGATIVES:
        examples.append(make_negative(neg))

    # Shuffle for realistic ordering
    random.shuffle(examples)
    return examples


# ─── Write ────────────────────────────────────────────────

if __name__ == "__main__":
    examples = generate_all()
    with open(OUTPUT, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    print(f"✅ Generated {len(examples)} examples → {OUTPUT}")

    # Print distribution
    from collections import Counter
    label_counts = Counter()
    total_spans = 0
    for ex in examples:
        for s in ex["spans"]:
            label_counts[s["label"]] += 1
            total_spans += 1
    negatives = sum(1 for ex in examples if not ex["spans"])
    print(f"\nDistribution:")
    print(f"  Total examples:  {len(examples)}")
    print(f"  Negatives (no PII): {negatives}")
    print(f"  Total gold spans: {total_spans}")
    print(f"\nPer-label counts:")
    for label, count in sorted(label_counts.items(), key=lambda x: -x[1]):
        print(f"  {label:<25} {count:>4}")
