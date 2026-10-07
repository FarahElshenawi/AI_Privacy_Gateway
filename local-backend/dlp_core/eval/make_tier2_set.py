"""Tier 2 / Tier 3 evaluation set: names, organizations, locations, addresses, usernames,
dates of birth and uncued phone numbers, in tech/finance-flavoured English.

Why a separate set: holdout_v1 has ~15 spans per semantic label, too few to rank models
(+/-0.15 on F1). This set has ~60-100 per label and many HARD NEGATIVES, because the failure that
matters for a tech-prompt gateway is false positives on tool names, code identifiers and names
that are also ordinary words ("Will", "Mark", "Jordan", "Chase").

PRODUCT DECISIONS embedded in the gold (change them here if yours differ):
  * Public tool/vendor names (GitHub, Kubernetes, AWS) are NOT sensitive -> negatives, tag public-tech-name.
  * Customer / employer names ARE sensitive -> ORGANIZATION.
  * Cities/countries are LOCATION (sensitive); generic words like "cloud region" are not.
Independence caveat: written by the same author as the detectors. Frozen by sha256 once generated.

    python -m dlp_core.eval.make_tier2_set        -> eval/tier2_v1.jsonl + .sha256
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

from .make_holdout import build

rng = random.Random(20261007)

FIRST = ("Olivia Liam Noor Mateo Aisha Chen Priya Lars Sofia Tariq Emma Yuki Amara Ibrahim Hana Diego Fatima Kenji "
         "Zainab Rohan Elena Omar Ingrid Kwame Mei Santiago Leila Dmitri Anika Tomas Nadia Jamal Freya Hiroshi "
         "Camila Arjun Beatrix Samir Ayesha Lucas Ximena Viktor Grace Ahmed Yara Sven Thandi Marco Ines Joon").split()
LAST = ("Walker Nguyen Haddad Rossi Kowalski Okafor Silva Brandt Patel Moreau Tanaka Hassan Petrov Lindqvist Mbeki "
        "Fernandez Cohen Yilmaz Abdi Larsen Romero Chaudhry Oyelaran Vasquez Becker Hoang Mahmoud Duarte Kim "
        "Alvarez Novak Singh Okoye Rahman Fischer Costa Banerjee Ivanov Mensah Gallo Park Ndlovu Haas Sato").split()
STEM = "Brightwave Cedar Vantor Lumen Northgate Helix Quartz Redwood Orion Tidewater Ironbridge Maple Solstice Kestrel Blueline Ardent Foxglove Granite".split()
SUFFIX = ["Systems", "Logistics", "Health Partners", "Capital", "Labs", "& Finch LLP", "Analytics", "Bank", "Freight", "Biotech", "Group", "Ltd"]
CITIES = [c.replace("_", " ") for c in "Rotterdam Austin Lisbon Nairobi Osaka Denver Cairo Seattle Toronto Dublin Mumbai Santiago Oslo Lagos Warsaw Hanoi Perth Zurich Bogota Tallinn Marseille Pune Accra Glasgow Kyoto Montreal Cape_Town Buenos_Aires".split()]
COUNTRIES = "Germany Brazil Kenya Japan Norway Egypt Canada Ireland Vietnam Chile Poland India".split()
STREETS = "Oak Maple Cedar Willow Harbor Station Mill Elm Lakeview Sunset Bridge Church".split()
ROLES = ["Head of Finance", "Security Engineer", "Account Manager", "CTO", "Data Analyst", "Procurement Lead"]


def name():
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def org():
    return f"{rng.choice(STEM)} {rng.choice(SUFFIX)}"


def city():
    return rng.choice(CITIES)


def country():
    return rng.choice(COUNTRIES)


def address():
    return f"{rng.randint(2, 998)} {rng.choice(STREETS)} {rng.choice(['Street', 'Avenue', 'Road', 'Lane'])}, {city()}"


def username():
    f, l = rng.choice(FIRST).lower(), rng.choice(LAST).lower()
    return rng.choice([f"{f[0]}{l}", f"{f}.{l[0]}", f"{f}_{l}{rng.randint(1, 99)}", f"{f}{rng.randint(10, 99)}"])


def dob():
    m, d, y = rng.randint(1, 12), rng.randint(1, 28), rng.randint(1950, 2004)
    mon = "January February March April May June July August September October November December".split()[m - 1]
    return rng.choice([f"{m:02d}/{d:02d}/{y}", f"{y}-{m:02d}-{d:02d}", f"{mon} {d}, {y}", f"{d} {mon} {y}"])


def phone_uncued():
    return rng.choice([f"{rng.randint(201, 989)} {rng.randint(201, 989)} {rng.randint(1000, 9999)}",
                       f"+44 7{rng.randint(100, 999)} {rng.randint(100000, 999999)}",
                       f"({rng.randint(201, 989)}) {rng.randint(201, 989)}-{rng.randint(1000, 9999)}"])


T_PERSON = ["Thanks, <<v>> from accounting will follow up.", "Reviewed by <<v>> on Friday.", "Ticket assigned to <<v>>.",
            "Author: <<v>> <dev@example.com>", "# TODO(<<v>>): remove after the migration", "Please loop in <<v>> before we ship.",
            "Meeting notes: <<v>> raised the audit finding.", "Dear <<v>>,\n\nYour invoice is attached.", "cc: <<v>>",
            "Hi, this is <<v>> calling about the refund.", "On Tuesday <<v>> approved the budget.", "{\"owner\": \"<<v>>\", \"status\": \"open\"}"]
T_ORG = ["We signed the contract with <<v>> last week.", "Invoice from <<v>> is overdue.", "I work at <<v>> as a contractor.",
         "Our customer <<v>> reported the outage.", "Wire the deposit to <<v>>.", "Pentest scope: internal apps owned by <<v>>.",
         "{\"client\": \"<<v>>\"}", "Re: renewal for <<v>>", "The audit of <<v>> starts Monday."]
T_LOC = ["Our office in <<v>> closes early today.", "Shipping to <<v>> takes five days.", "He relocated to <<v>> in March.",
         "The data center is in <<v>>, near the client.", "Flight to <<v>> was cancelled.", "{\"city\": \"<<v>>\"}"]
T_ADDR = ["Ship it to <<v>> please.", "Billing address: <<v>>", "Customer lives at <<v>>.", "Deliver to <<v>> before noon."]
T_USER = ["Login failed for user <<v>> from the VPN.", "ssh <<v>>@build-host", "Slack: @<<v>> will pick this up.", "git config user.name \"<<v>>\"",
          "{\"username\": \"<<v>>\"}"]
T_DOB = ["Patient DOB: <<v>>.", "Born on <<v>>, according to the file.", "Date of birth <<v>> (verified).", "{\"dob\": \"<<v>>\"}"]
T_PHONE = ["You can reach me on <<v>> after five.", "My number is <<v>>, text first.", "Ring me on <<v>> tomorrow.", "Reply to <<v>> if urgent."]

# Negatives: the point of the set. Public tools, code identifiers, names used as ordinary words.
TECH = ["We deploy to Kubernetes on AWS with Terraform and GitHub Actions.", "Docker build fails on Jenkins after the Python 3.12 upgrade.",
        "Kafka lag on the Elasticsearch cluster in us-east-1 is rising.", "The Postgres migration ran on Amazon RDS overnight.",
        "Use Slack for incident chatter and Jira for tickets.", "Azure DevOps pipelines call the Stripe sandbox.",
        "Install Node.js, then run npm ci inside the React app.", "Oracle and SAP connectors are in the integration backlog.",
        "Switch the CDN from Cloudflare to Fastly if latency regresses.", "Spark jobs on Databricks read from Snowflake.",
        "Our Salesforce sync failed after the Okta change.", "The Linux kernel panic came from a Nvidia driver.",
        "Chrome and Firefox render the Tailwind page differently.", "Grafana dashboards pull from Prometheus.",
        "Terraform state lives in an S3 bucket in the Frankfurt region.", "Redis, MongoDB and Cassandra were benchmarked."]
WORDS = ["Will you merge this before the release?", "Please mark the ticket as resolved.", "Grant read access to the bucket.",
         "Bill the customer at the end of the month.", "We should chase the vendor for the SLA report.", "The rose chart shows sales by region.",
         "Jack up the timeout to 30 seconds.", "Wells in the dataset are labelled by depth.", "Pat the buffer before copying it.",
         "Hunter-gatherer is a model in the test fixtures.", "Dean's list is a column in the students table.", "Frank discussion about the roadmap.",
         "The bank of switches is rack 4.", "Rich text and plain text exports differ.", "April is the fiscal year start.",
         "Jordan, Nile and Amazon are names in our rivers dataset.", "Victoria station is a test string for the parser.",
         "Cash in the ledger is reconciled daily.", "Chase the bug through the stack trace."]
CODE = ["def paris_distance(a, b): return haversine(a, b)", "class Customer(BaseModel): name: str; email: str",
        "SELECT first_name, last_name FROM users WHERE id = 42;", "user_name = request.form['user_name']  # placeholder",
        "const city = props.city || 'N/A';", "log.info('processing order for customer_id=%s', cid)",
        "// John Doe is the example name used in the README", "name: str = Field(..., description='full name')",
        "export const COUNTRIES = ['Germany', 'France'];  // enum for the dropdown UI",
        "git checkout -b feature/new-york-layout", "docker run --name my-postgres -e POSTGRES_USER=app postgres:16",
        "assert get_city('London') == 'London'  # unit test"]
GENERIC = ["The quarterly report is due on Friday afternoon.", "Please summarise the attached design document.",
           "Latency p95 improved from 480 ms to 310 ms after the cache change.", "Explain the difference between TCP and UDP.",
           "What is the best way to structure a monorepo?", "The migration plan has three phases and a rollback."]

# positives where an ambiguous name is REAL (so models can't just ignore common-word names)
AMBIG_PERSON = ["Jordan Reyes", "Chase Mitchell", "Will Anderson", "Mark Ellison", "Rose Hartley", "Grant Osei", "Bill Okonkwo", "Jack Moreau", "Wells Park"]


def case(tpl, slots, tags):
    return build(tpl, slots, tags, "")


def main(out_dir: Path) -> None:
    cases: list[dict] = []
    for t in T_PERSON:
        for _ in range(7):
            cases.append(case(t, {"v": (name(), "PERSON", 3)}, ["person", "diverse-name"]))
    for t in T_PERSON[:5]:
        for n in AMBIG_PERSON[:5]:
            cases.append(case(t, {"v": (n, "PERSON", 3)}, ["person", "common-word-name"]))
    for t in T_ORG:
        for _ in range(7):
            cases.append(case(t, {"v": (org(), "ORGANIZATION", 2)}, ["organization"]))
    for t in T_LOC:
        for _ in range(8):
            cases.append(case(t, {"v": (rng.choice([city(), country()]), "LOCATION", 2)}, ["location"]))
    for t in T_ADDR:
        for _ in range(8):
            cases.append(case(t, {"v": (address(), "ADDRESS", 2)}, ["address"]))
    for t in T_USER:
        for _ in range(7):
            cases.append(case(t, {"v": (username(), "USERNAME", 2)}, ["username"]))
    for t in T_DOB:
        for _ in range(8):
            cases.append(case(t, {"v": (dob(), "DATE_OF_BIRTH", 2)}, ["dob"]))
    for t in T_PHONE:
        for _ in range(8):
            cases.append(case(t, {"v": (phone_uncued(), "PHONE_NUMBER", 2)}, ["uncued-phone"]))
    for _ in range(25):
        cases.append(case("Best regards,\n<<n>>\n<<r>>, <<o>>\n<<c>>", {
            "n": (name(), "PERSON", 3), "r": (rng.choice(ROLES), "TITLE", 0), "o": (org(), "ORGANIZATION", 2), "c": (city(), "LOCATION", 2)},
            ["signature", "mixed"]))
    for _ in range(20):
        cases.append(case("<<n>> (<<u>>) from <<o>> asked about the <<c>> office; DOB <<d>>.", {
            "n": (name(), "PERSON", 3), "u": (username(), "USERNAME", 2), "o": (org(), "ORGANIZATION", 2),
            "c": (city(), "LOCATION", 2), "d": (dob(), "DATE_OF_BIRTH", 2)}, ["mixed"]))
    # repeated mention of one person: every occurrence must be masked
    for _ in range(12):
        n = name()
        cases.append(case("<<a>> opened the ticket. Later <<b>> added a note, and <<c>> closed it.",
                          {"a": (n, "PERSON", 3), "b": (n, "PERSON", 3), "c": (n, "PERSON", 3)}, ["repeated-person"]))
    for s in TECH:
        cases.append({"id": "", "text": s, "tags": ["benign", "public-tech-name"], "spans": []})
    for s in WORDS:
        cases.append({"id": "", "text": s, "tags": ["benign", "common-word-name"], "spans": []})
    for s in CODE:
        cases.append({"id": "", "text": s, "tags": ["benign", "code"], "spans": []})
    for s in GENERIC:
        cases.append({"id": "", "text": s, "tags": ["benign", "generic"], "spans": []})
    for c in cases:                                    # the "TITLE" slot is context, not sensitive: drop it from gold
        c["spans"] = [s for s in c["spans"] if s["label"] != "TITLE"]
    for i, c in enumerate(cases):
        c["id"] = f"t2-{i:04d}"
        assert all(c["text"][s["start"]:s["end"]] for s in c["spans"])
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "tier2_v1.jsonl"
    p.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    (out_dir / "tier2_v1.sha256").write_text(digest + "\n")
    from collections import Counter
    n_by = Counter(s["label"] for c in cases for s in c["spans"])
    print(f"wrote {len(cases)} cases -> {p}\nsha256 {digest}\ngold spans: {dict(n_by)}\nbenign cases: {sum(1 for c in cases if not c['spans'])}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "eval")
