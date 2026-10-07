"""Hold-out v1 generator. FROZEN once written: never tune detectors against it.

Independence caveat (stated plainly): this generator was written after the Tier 1 patterns,
by the same author, so it is independent of D3 and of eval_set_v2's generator, but NOT of the
author's assumptions. Treat it as a regression/hold-out set; add real, customer-style samples
(and an outside reviewer's cases) before quoting numbers externally.

Values are generated, not copied: Luhn/IBAN/ABA/base58check checksums are computed.
Offsets come from a builder, so they are correct by construction.

    python -m dlp_core.eval.make_holdout          -> eval/holdout_v1.jsonl + .sha256
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import string
import sys
from pathlib import Path

SEED = 20261006
rng = random.Random(SEED)
ALNUM = string.ascii_letters + string.digits
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# ------------------------------------------------------------------ value generators
def luhn_complete(prefix: str, length: int) -> str:
    body = prefix + "".join(rng.choice(string.digits) for _ in range(length - len(prefix) - 1))
    total = 0
    for i, ch in enumerate(reversed(body)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
    return body + str((10 - total % 10) % 10)


def gen_card() -> str:
    kind = rng.choice(["visa", "visa", "mc", "amex", "disc"])
    num = {"visa": lambda: luhn_complete("4", 16), "mc": lambda: luhn_complete(str(rng.randint(51, 55)), 16),
           "amex": lambda: luhn_complete(rng.choice(["34", "37"]), 15),
           "disc": lambda: luhn_complete("6011", 16)}[kind]()
    sep = rng.choice(["", " ", "-", " "])
    if not sep:
        return num
    size = (4, 6, 5) if len(num) == 15 else (4, 4, 4, 4)
    out, i = [], 0
    for s in size:
        out.append(num[i:i + s]); i += s
    return sep.join(out + ([num[i:]] if i < len(num) else []))


def gen_iban() -> str:
    cc, bban = rng.choice([
        ("DE", lambda: "".join(rng.choices(string.digits, k=18))),
        ("GB", lambda: "".join(rng.choices(string.ascii_uppercase, k=4)) + "".join(rng.choices(string.digits, k=14))),
        ("NL", lambda: "".join(rng.choices(string.ascii_uppercase, k=4)) + "".join(rng.choices(string.digits, k=10))),
        ("ES", lambda: "".join(rng.choices(string.digits, k=20))),
        ("BE", lambda: "".join(rng.choices(string.digits, k=12))),
    ])
    b = bban()
    num = "".join(str(int(c, 36)) for c in b + cc + "00")
    iban = f"{cc}{98 - int(num) % 97:02d}{b}"
    return iban if rng.random() < .7 else " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def gen_aba() -> str:
    while True:
        d = [int(c) for c in f"{rng.choice([1, 2, 3, 6, 7, 11, 12, 21, 22, 26, 31, 61, 71]):02d}"]
        d += [rng.randint(0, 9) for _ in range(6)]
        w = (3, 7, 1, 3, 7, 1, 3, 7)
        d.append((-sum(a * b for a, b in zip(d, w))) % 10)
        s = "".join(map(str, d))
        if len(s) == 9:
            return s


def gen_ssn() -> str:
    a = rng.choice([x for x in range(1, 900) if x != 666])
    return f"{a:03d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"


def gen_btc() -> str:
    raw = b"\x00" + rng.randbytes(20)
    raw += hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


def gen_eth() -> str:
    return "0x" + rng.randbytes(20).hex()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def gen_jwt() -> str:
    h = b64u(json.dumps({"alg": rng.choice(["HS256", "RS256"]), "typ": "JWT"}).encode())
    p = b64u(json.dumps({"sub": str(rng.randint(10**6, 10**9)), "iat": rng.randint(1_600_000_000, 1_800_000_000)}).encode())
    return f"{h}.{p}.{b64u(rng.randbytes(32))}"


def rs(alpha: str, n: int) -> str:
    return "".join(rng.choices(alpha, k=n))


API_KEYS = [lambda: "AKIA" + rs(string.ascii_uppercase + string.digits, 16),
            lambda: "ghp_" + rs(ALNUM, 36), lambda: "sk_live_" + rs(ALNUM, 24),
            lambda: "sk-proj-" + rs(ALNUM + "_-", 48), lambda: "AIza" + rs(ALNUM + "_-", 35),
            lambda: "xoxb-" + rs(string.digits, 11) + "-" + rs(ALNUM, 24),
            lambda: "glpat-" + rs(ALNUM, 20), lambda: "hf_" + rs(ALNUM, 34)]
FIRST = ["Olivia", "Liam", "Noor", "Mateo", "Aisha", "Chen", "Priya", "Lars", "Sofia", "Tariq", "Emma", "Yuki"]
LAST = ["Walker", "Nguyen", "Haddad", "Rossi", "Kowalski", "Okafor", "Silva", "Brandt", "Patel", "Moreau"]
ORGS = ["Northwind Traders", "Helios Biotech", "Blue Harbor Bank", "Quantix Labs", "Orion Freight", "Lumen Health"]
CITIES = ["Rotterdam", "Austin", "Lisbon", "Nairobi", "Osaka", "Denver", "Cairo", "Seattle"]
DOMAINS = ["example.com", "mail.example.org", "corp-mail.net", "contoso.io", "fabrikam.co.uk"]


def gen_email() -> str:
    local = rng.choice([f"{rng.choice(FIRST)}.{rng.choice(LAST)}", f"{rng.choice(FIRST)[0]}{rng.choice(LAST)}",
                        f"user{rng.randint(10, 9999)}", f"{rng.choice(FIRST)}+{rs(string.ascii_lowercase, 4)}"]).lower()
    return f"{local}@{rng.choice(DOMAINS)}"


def gen_phone() -> str:
    a, b, c = rng.randint(201, 989), rng.randint(201, 989), rng.randint(1000, 9999)
    return rng.choice([f"+1{a}{b}{c}", f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"+44{rng.randint(2000000000, 7999999999)}",
                       f"+1 {a} {b} {c}"])


def gen_ip() -> str:
    return rng.choice([f"10.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(1,254)}",
                       f"192.168.{rng.randint(0,255)}.{rng.randint(1,254)}",
                       f"{rng.randint(11,99)}.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(1,254)}"])


def gen_name() -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


# ------------------------------------------------------------------ builder
SLOT = re.compile(r"<<(\w+)>>")


def build(template: str, slots: dict[str, tuple[str, str, int]], tags: list[str], cid: str) -> dict:
    """slots: name -> (value, label, tier). Offsets are computed while assembling."""
    text, spans, pos = "", [], 0
    for m in SLOT.finditer(template):
        text += template[pos:m.start()]
        value, label, tier = slots[m.group(1)]
        spans.append({"start": len(text), "end": len(text) + len(value), "label": label, "tier": tier})
        text += value
        pos = m.end()
    text += template[pos:]
    return {"id": cid, "text": text, "tags": tags, "spans": spans}


# ------------------------------------------------------------------ templates
PROSE = ["Hi team, <<v>> please review.", "Customer says <<v>> and wants a refund.",
         "FYI: <<v>>", "Pasting from the ticket: <<v>>.", "{\"note\": \"<<v>>\"}"]


def wrap(cue: str, label: str, gen, tier=1, tags=()):
    """cue-bearing contexts for entities that need a keyword."""
    ctxs = [f"{cue} <<v>>", f'{{"{cue.strip(":= ").lower().replace(" ", "_")}": "<<v>>"}}',
            f"- {cue} <<v>>", f"[INFO] user update {cue} <<v>> ok", f"{cue.upper()} <<v>>, thanks"]
    return [(c, label, gen, tier, list(tags)) for c in ctxs]


ENTITY_CASES: list[tuple[str, str, object, int, list[str]]] = []
for c in PROSE:
    ENTITY_CASES += [(c, "CREDIT_CARD", gen_card, 1, ["prose"]), (c, "EMAIL", gen_email, 1, ["prose"]),
                     (c, "IBAN", gen_iban, 1, ["prose"]), (c, "US_SSN", gen_ssn, 1, ["prose", "ssn-bare"]),
                     (c, "CRYPTO_WALLET", gen_btc, 1, ["prose"]), (c, "AUTH_TOKEN", gen_jwt, 1, ["prose"]),
                     (c, "IP_ADDRESS", gen_ip, 1, ["prose"])]
ENTITY_CASES = [(c, l, g, t, tg) for (c, l, g, t, tg) in ENTITY_CASES if not (l == "US_SSN" and "ssn-bare" in tg and "{" in c)]
for cue in ["Routing number:", "ABA", "routing:"]:
    ENTITY_CASES += wrap(cue, "ABA_ROUTING", gen_aba, tags=["cued"])
for cue in ["SSN:", "social security number is", "SSN"]:
    ENTITY_CASES += wrap(cue, "US_SSN", gen_ssn, tags=["cued"])
for cue in ["phone:", "Tel:", "mobile", "call me at", "Contact number:", "WhatsApp"]:
    ENTITY_CASES += wrap(cue, "PHONE_NUMBER", gen_phone, tags=["cued-phone"])
for t in PROSE[:3]:
    ENTITY_CASES.append((t, "PHONE_NUMBER", gen_phone, 2, ["uncued-phone"]))
ENV = ["export <<v>>", "API_KEY=<<v>>", "token: <<v>>", "curl -H 'X-Api-Key: <<v>>' https://api.example.com",
       "  key: \"<<v>>\"", "Authorization: Bearer <<v>>"]
for t in ENV:
    for gen in rng.sample(API_KEYS, 4):
        ENTITY_CASES.append((t.replace("export <<v>>", "export KEY=<<v>>"), "API_KEY", gen, 1, ["secret"]))
for t in ["DB_URL=<<v>>", "connection: <<v>>", "postgres at <<v>> now"]:
    for _ in range(4):
        v = f"postgresql://{rng.choice(['app', 'svc', 'admin'])}:{rs(ALNUM, 14)}@db{rng.randint(1,9)}.internal:5432/{rng.choice(['prod', 'app'])}"
        ENTITY_CASES.append((t, "CONNECTION_STRING", lambda v=v: v, 1, ["secret"]))
for t in ["password: <<v>>", "pwd=<<v>>", "The admin password is <<v>>"]:
    for _ in range(4):
        ENTITY_CASES.append((t, "PASSWORD", lambda: rs(ALNUM, 12) + rng.choice("!#%"), 1, ["secret"]))
for t in PROSE[:3] + ["acct <<v>>"]:
    for _ in range(3):
        ENTITY_CASES.append(("Account number: <<v>>" if "acct" in t else t.replace("<<v>>", "bank account <<v>>"),
                             "BANK_ACCOUNT_NUMBER", lambda: rs(string.digits, rng.randint(8, 12)), 1, ["cued"]))
for t in PROSE[:3]:
    for _ in range(2):
        ENTITY_CASES.append((t.replace("<<v>>", "EIN <<v>>"), "TAX_ID",
                             lambda: f"{rng.choice([12, 20, 27, 31, 45, 52, 61, 75, 84, 91])}-{rs(string.digits, 7)}", 1, ["cued"]))
        ENTITY_CASES.append((t.replace("<<v>>", "MRN: <<v>>"), "MEDICAL_RECORD_NUMBER",
                             lambda: "MRN-" + rs(string.digits, 7), 1, ["cued"]))
for t in PROSE[:3] + ["Internal wiki: <<v>>"]:
    for _ in range(2):
        ENTITY_CASES.append((t, "INTERNAL_URL", lambda: f"https://{rng.choice(['wiki', 'git', 'ci'])}{rng.randint(1,9)}.{rng.choice(['internal', 'corp', 'lan'])}/{rs(string.ascii_lowercase, 6)}", 1, ["infra"]))
for t in PROSE[:2]:
    for _ in range(3):
        ENTITY_CASES.append((t, "PERSON", gen_name, 3, ["names"]))
        ENTITY_CASES.append((t, "ORGANIZATION", lambda: rng.choice(ORGS), 2, ["names"]))
        ENTITY_CASES.append((t, "LOCATION", lambda: rng.choice(CITIES), 2, ["names"]))


def card_extras() -> list[dict]:
    out = []
    for i in range(14):
        card, cvv = gen_card(), rng.choice(["123", "456", "789", "0421", "902"])
        exp = f"{rng.randint(1, 12):02d}/{rng.choice(['26', '27', '28', '2029'])}"
        form = i % 4
        if form == 0:
            out.append(build("Card <<c>>, CVV <<v>>, exp <<e>>", {"c": (card, "CREDIT_CARD", 1), "v": (cvv, "CVV", 1), "e": (exp, "CARD_EXPIRY", 1)}, ["card-bundle"], ""))
        elif form == 1:
            out.append(build("cc: <<c>> | exp: <<e>> | cvc: <<v>>", {"c": (card, "CREDIT_CARD", 1), "v": (cvv, "CVV", 1), "e": (exp, "CARD_EXPIRY", 1)}, ["card-bundle"], ""))
        elif form == 2:
            out.append(build("security code <<v>>", {"v": (cvv, "CVV", 1)}, ["cued"], ""))
        else:
            out.append(build("valid thru <<e>>", {"e": (exp, "CARD_EXPIRY", 1)}, ["cued"], ""))
    return out


def hard_cases() -> list[dict]:
    out = []
    # unicode / spacing obfuscation
    for _ in range(4):
        c = gen_card().replace(" ", "").replace("-", "")
        groups = [c[i:i + 4] for i in range(0, 16, 4)] if len(c) == 16 else [c]
        value = "\u200b ".join(groups) if len(groups) > 1 else c
        out.append(build("pay with <<c>> today", {"c": (value, "CREDIT_CARD", 1)}, ["obfuscated"], ""))
    for _ in range(3):
        c = gen_card().replace(" ", "").replace("-", "")
        wide = "".join(chr(ord(ch) + 0xFEE0) for ch in c)
        out.append(build("card <<c>>", {"c": (wide, "CREDIT_CARD", 1)}, ["obfuscated"], ""))
    # double-space (PDF extraction) and wrapped lines
    for _ in range(4):
        c = luhn_complete("4", 16)
        out.append(build("Visa  <<c>>  exp", {"c": ("  ".join(c[i:i + 4] for i in range(0, 16, 4)), "CREDIT_CARD", 1)}, ["pdf-extracted"], ""))
    for _ in range(4):
        c = luhn_complete("4", 16)
        out.append(build("Card number:\n<<c>>\nExpires soon", {"c": (f"{c[:8]}\n{c[8:]}", "CREDIT_CARD", 1)}, ["wrapped", "hard"], ""))
    # multi-entity logs / JSON / YAML
    for _ in range(8):
        e, ip, k = gen_email(), gen_ip(), rng.choice(API_KEYS)()
        out.append(build('2026-10-01T12:00:03Z INFO login ok user=<<e>> src=<<ip>> key=<<k>> status=200',
                         {"e": (e, "EMAIL", 1), "ip": (ip, "IP_ADDRESS", 1), "k": (k, "API_KEY", 1)}, ["log"], ""))
    for _ in range(6):
        e, c = gen_email(), gen_card()
        out.append(build('{"user": {"email": "<<e>>", "payment": {"card": "<<c>>", "currency": "USD"}}}',
                         {"e": (e, "EMAIL", 1), "c": (c, "CREDIT_CARD", 1)}, ["json"], ""))
    for _ in range(6):
        s, i = gen_ssn(), gen_iban()
        out.append(build("employee:\n  ssn: <<s>>\n  iban: <<i>>\n  dept: finance", {"s": (s, "US_SSN", 1), "i": (i, "IBAN", 1)}, ["yaml"], ""))
    for _ in range(5):
        out.append(build("csv: id,email,btc\n7,<<e>>,<<b>>\n", {"e": (gen_email(), "EMAIL", 1), "b": (gen_btc(), "CRYPTO_WALLET", 1)}, ["csv"], ""))
    # prose with names + secrets
    for _ in range(6):
        out.append(build("Hello, I'm <<n>> from <<o>> in <<l>>. Reach me at <<e>>.",
                         {"n": (gen_name(), "PERSON", 3), "o": (rng.choice(ORGS), "ORGANIZATION", 2),
                          "l": (rng.choice(CITIES), "LOCATION", 2), "e": (gen_email(), "EMAIL", 1)}, ["names", "mixed"], ""))
    for _ in range(3):
        pem_body = "\n".join(base64.b64encode(rng.randbytes(48)).decode() for _ in range(3))
        key = f"-----BEGIN PRIVATE KEY-----\n{pem_body}\n-----END PRIVATE KEY-----"
        out.append(build("here is the key:\n<<k>>\nthanks", {"k": (key, "PRIVATE_KEY", 1)}, ["secret", "multiline"], ""))
    return out


NEGATIVES = [
    "Build 8f3a2c1d9e7b4a60 passed on main in 4m12s.", "UUID: 550e8400-e29b-41d4-a716-446655440000 is the request id.",
    "git commit 3f786850e387550fdab836ed7e6dc881de23001b fixes the bug.", "Version 10.2.4-rc1 shipped on 2026-09-30.",
    "Order 7731904482 shipped; tracking 1Z999AA10123456784.", "The ISBN 978-3-16-148410-0 is on page 114.",
    "ratio 16:9, 1920x1080 at 59.94 fps, bitrate 8500 kbps", "Meeting moved to 14:30 on 03/07, room 4B.",
    "x = 0.1234567890123456; y = 3.141592653589793", "see https://docs.example.com/guide/getting-started#install",
    "Set password = os.environ['DB_PASSWORD'] before starting.", "api_key = your_api_key_here  # replace me",
    "token = None  # set at runtime", "const SECRET = process.env.SECRET_KEY;", "password: ***** (hidden)",
    "ping 127.0.0.1 and 0.0.0.0 for the loopback test", "IPv4 mask 255.255.255.0 and version 1.2.3",
    "The timestamp 1759999999999 is epoch millis.", "Invoice #2026-000451 total $1,234.56 due net 30.",
    "Hex 0xDEADBEEF and color #1A2B3C are not secrets.", "The sha256 prefix e3b0c44298fc1c14 is the empty hash.",
    "Call stack: foo.bar.baz at line 42 in module.py", "email is optional; leave the field empty",
    "SELECT id, name FROM users WHERE id = 42;", "Release notes: fixed crash when version < 3.0.1",
    "Lat 37.7749, lon -122.4194 (San Francisco)", "Phone-friendly layout for screens 360x640", "pi=3.14159, e=2.71828, phi=1.61803",
    "Meeting ID 123 456 7890 passcode shared in calendar", "Part number AB-4421-XZ, batch 20260901",
    "The function call(1234567) returns 7 digits", "Reference REF-2026-0007-ABC for your records.",
    "def luhn(n): return sum(map(int, str(n))) % 10 == 0", "Zip 94105, area code 415, ext 3321",
    "Just a normal sentence with nothing sensitive in it at all.", "Please read chapter 12, verses 4 through 9 tonight.",
    "uuid4 -> 9b2f1c7e-5a3d-4e8f-8c2a-1d4e6f708192", "CI job #88231 took 7m and used 2.4GB",
    "The quick brown fox jumps over the lazy dog 1234567890 times.", "Docs: set `expires: 2026-12-31` in the config file",
]


def main(out_dir: Path) -> None:
    cases: list[dict] = []
    for i, (tpl, label, gen, tier, tags) in enumerate(ENTITY_CASES):
        v = gen()
        cases.append(build(tpl, {"v": (v, label, tier)}, tags, ""))
    cases += card_extras() + hard_cases()
    for n in NEGATIVES:
        cases.append({"id": "", "text": n, "tags": ["benign"], "spans": []})
    for i, c in enumerate(cases):
        c["id"] = f"h1-{i:04d}"
        for s in c["spans"]:
            assert c["text"][s["start"]:s["end"]], c
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "holdout_v1.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (out_dir / "holdout_v1.sha256").write_text(digest + "\n")
    print(f"wrote {len(cases)} cases, {sum(len(c['spans']) for c in cases)} gold spans -> {path}\nsha256 {digest}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "eval")
