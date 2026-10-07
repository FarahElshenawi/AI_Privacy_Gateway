"""Dev set v1: hand-written, real-world-shaped prompts. THIS is the set you may tune against.

Different style from holdout_v1 (which stays frozen): realistic snippets (HTTP, XML, query
strings, .env, YAML, JSON, markdown tables, support chats) with the same entity types in
many carrier formats, plus look-alike negatives. Values come from the shared generators.

    python -m dlp_core.eval.make_dev
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from . import make_holdout as H

H.rng.seed(777)
g = H


def s(label, gen, tier=1):
    return (gen, label, tier)


T = []   # (template, slots, tags)


def add(tpl, tags, **slots):
    T.append((tpl, slots, tags))


# cued entities in machine-readable carriers
for _ in range(2):
    add('{"routing_number": "<<a>>", "account_number": "<<b>>"}', ["json", "cued"],
        a=s("ABA_ROUTING", g.gen_aba), b=s("BANK_ACCOUNT_NUMBER", lambda: g.rs("0123456789", 10)))
    add("routing_number=<<a>>&acct=<<b>>", ["query", "cued"],
        a=s("ABA_ROUTING", g.gen_aba), b=s("BANK_ACCOUNT_NUMBER", lambda: g.rs("0123456789", 9)))
    add('{"ssn": "<<s>>"}', ["json", "cued", "ssn-bare"], s=s("US_SSN", lambda: g.gen_ssn().replace("-", "")))
    add("ssn: <<s>>", ["yaml", "cued"], s=s("US_SSN", g.gen_ssn))
    add('{"card": "<<c>>", "cvv": "<<v>>", "expiry_date": "<<e>>"}', ["json", "card-bundle"],
        c=s("CREDIT_CARD", g.gen_card), v=s("CVV", lambda: str(g.rng.randint(100, 999))),
        e=s("CARD_EXPIRY", lambda: f"{g.rng.randint(1,12):02d}/{g.rng.choice(['27','28','2029'])}"))
    add("card_cvv=<<v>>&card_exp=<<e>>", ["query", "cued"], v=s("CVV", lambda: str(g.rng.randint(100, 999))),
        e=s("CARD_EXPIRY", lambda: f"{g.rng.randint(1,12):02d}/{g.rng.choice(['27','28'])}"))
    add("<customer><phone><<p>></phone><email><<e>></email></customer>", ["xml", "cued-phone"],
        p=s("PHONE_NUMBER", g.gen_phone), e=s("EMAIL", g.gen_email))
    add("phone_number=<<p>>&email=<<e>>&plan=pro", ["query", "cued-phone"],
        p=s("PHONE_NUMBER", g.gen_phone), e=s("EMAIL", g.gen_email))
    add('user_phone: "<<p>>"', ["yaml", "cued-phone"], p=s("PHONE_NUMBER", g.gen_phone))
    add('{"mobile": "<<p>>", "fax": "<<q>>"}', ["json", "cued-phone"],
        p=s("PHONE_NUMBER", g.gen_phone), q=s("PHONE_NUMBER", g.gen_phone))
    add('{"tax_id": "<<t>>", "mrn": "<<m>>"}', ["json", "cued"],
        t=s("TAX_ID", lambda: f"{g.rng.choice([12,20,27,31,45,52])}-{g.rs('0123456789',7)}"),
        m=s("MEDICAL_RECORD_NUMBER", lambda: "MRN" + g.rs("0123456789", 7)))
    add('{"member_id": "<<m>>"}', ["json", "cued"], m=s("HEALTH_INSURANCE_ID", lambda: "XQ" + g.rs("0123456789", 8)))
    add('{"password": "<<p>>", "remember": true}', ["json", "secret"], p=s("PASSWORD", lambda: g.rs(g.ALNUM, 12) + "!"))
    add("password=<<p>>&user=admin", ["query", "secret"], p=s("PASSWORD", lambda: g.rs(g.ALNUM, 11) + "#"))
    add("X-Api-Key: <<k>>", ["http", "secret"], k=s("API_KEY", g.rng.choice(g.API_KEYS)))
    add("Authorization: Bearer <<j>>", ["http", "secret"], j=s("AUTH_TOKEN", g.gen_jwt))
    add("SWIFT code: <<w>>", ["cued"], w=s("SWIFT_BIC", lambda: g.rng.choice(["CHASUS33", "DEUTDEFF", "BARCGB22", "HSBCGB2L"])))
# wrapped / pdf-extracted / table carriers
for _ in range(3):
    add("Card number:\n<<c>>\nValid thru <<e>>", ["wrapped"],
        c=s("CREDIT_CARD", lambda: (lambda n: f"{n[:8]}\n{n[8:]}")(g.luhn_complete("4", 16))),
        e=s("CARD_EXPIRY", lambda: "11/28"))
    add("| Field | Value |\n| Card | <<c>> |\n| Email | <<e>> |", ["table"],
        c=s("CREDIT_CARD", g.gen_card), e=s("EMAIL", g.gen_email))
    add("Wire to <<i>> please, ref 88231.", ["prose"], i=s("IBAN", g.gen_iban))
    add("My BTC wallet is <<w>> (do not share)", ["prose"], w=s("CRYPTO_WALLET", g.gen_btc))
    add("Gateway <<ip>>, mask 255.255.255.0, dns <<d>>", ["network"], ip=s("IP_ADDRESS", lambda: f"10.0.{g.rng.randint(1,200)}.1"), d=s("IP_ADDRESS", lambda: "8.8.8.8"))
    add("DB_URL=<<c>>", ["env", "secret"], c=s("CONNECTION_STRING", lambda: f"mysql://root:{g.rs(g.ALNUM,12)}@10.1.{g.rng.randint(1,9)}.5:3306/app"))
    add("Hey, it's <<n>> at <<o>>, call me at <<p>> or write <<e>>.", ["names", "mixed"],
        n=s("PERSON", g.gen_name, 3), o=s("ORGANIZATION", lambda: g.rng.choice(g.ORGS), 2),
        p=s("PHONE_NUMBER", g.gen_phone), e=s("EMAIL", g.gen_email))

NEG = [
    '{"id": 12345678, "phone_model": "Pixel 8", "version": "1.2.3", "mask": "255.255.255.0"}',
    '{"password_min_length": 12, "password_policy": "strict", "routing": "edge-proxy"}',
    "txn=4000000000000008 status=declined retry in 30s", "call 911 immediately if there is smoke",
    "Set the phone to silent and the tel prompt to off.", "account created 2026-10-01 by admin",
    "curl -s https://api.example.com/v2/items?limit=50&offset=100", "Use mask 255.255.0.0 for the VPN pool",
    "user_agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "ssn_field_label: 'Social Security'",
    "cvv is a 3 or 4 digit code printed on the back of the card", "Expiry policy: tokens expire after 24h",
    "sha1 da39a3ee5e6b4b0d3255bfef95601890afd80709 for the artifact", "build-2026.10.06-rc2 deployed to staging",
    "order_id=77310044821 customer_ref=ACME-5521 qty=3", "price 1,299.00 USD incl. 14% VAT, SKU AB-4421-XZ",
    "tel aviv is a city; mobile apps and cellular networks were discussed", "Please contact support via the portal.",
]


def main(out_dir: Path) -> None:
    cases = []
    for i, (tpl, slots, tags) in enumerate(T):
        built = {k: (gen(), label, tier) for k, (gen, label, tier) in slots.items()}
        c = H.build(tpl, built, tags, f"d1-{i:04d}")
        cases.append(c)
    for n in NEG:
        cases.append({"id": f"d1-{len(cases):04d}", "text": n, "tags": ["benign"], "spans": []})
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "dev_v1.jsonl"
    p.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    print(f"wrote {len(cases)} cases, {sum(len(c['spans']) for c in cases)} gold spans -> {p}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "eval")
