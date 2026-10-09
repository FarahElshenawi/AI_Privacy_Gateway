#!/usr/bin/env python3
"""Synthetic fine-tuning data for the Tier 2 (GLiNER2-PII) semantic detector.

Targets ONLY the labels Tier 1 cannot catch (see dlp_core/tier2/config.py):
person, date_of_birth, sensitive_date, address, city, state_or_region,
postal_code, country, username, password, secret, government_id,
passport_number, drivers_license_number, organization, phone_number.

v2: titles ("Dr.") stay OUTSIDE the person span; organization/phone_number are labelled; many technical
hard negatives (hashes, ISBNs, env vars, timestamps...); long passwords/passphrases in several languages;
state examples; a leakage guard that skips any text containing a value from your hold-out files.

Outputs (GLiNER2 training format, one JSON object per line):
  train.jsonl            GLiNER2 training format: {"input": text, "output": {"entities": {label: [mentions]}}}
  semantic_test_v2.jsonl repo eval format (id/text/tags/spans+tier) built from DISJOINT names/streets/templates
  dev.jsonl              (only with --write-dev-jsonl) validation split in GLiNER2 training format

Hard negatives are emitted with an EMPTY list for the label that must NOT fire,
e.g. {"person": []} for "The Jordan River flows into the Dead Sea".

Usage:
  python generate_gliner_ft_data.py --n-train 3000 --n-dev 300 --seed 7 \
      --exclude src/dlp_core/eval/holdout_v1.jsonl src/eval/eval_set_v2.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from pathlib import Path

# --------------------------------------------------------------------------
# Value pools (dev gets every 5th item, train gets the rest -> disjoint)
# --------------------------------------------------------------------------
FIRST = ("Farah Omar Mariam Youssef Nour Karim Layla Hassan Salma Ahmed Mona Tarek Hana Khaled Dina "
         "Ali Rania Mostafa Yara Sherif Sarah Michael Emily David Jessica Daniel Olivia James Sophia "
         "Robert Priya Arjun Wei Mei Hiroshi Yuki Chen Li Ananya Rahul Carlos Lucia Mateo Valentina "
         "Diego Sofia Jean Camille Pierre Amelie Hans Greta Lukas Anika Ivan Olga Dmitri Natasha "
         "Kwame Amara Chinedu Zainab").split()
LAST = ("Hassan Mahmoud Ibrahim Soliman Farouk Abdelrahman Nasser Saleh Mansour Fahmy Smith Johnson "
        "Williams Brown Garcia Miller Davis Martinez Anderson Taylor Patel Sharma Singh Kumar Wang "
        "Zhang Tanaka Sato Kim Park Nguyen Rossi Ferrari Dubois Martin Bernard Schmidt Mueller Weber "
        "Fischer Ivanov Petrov Kowalski Novak Okafor Mensah Diallo Lopez Hernandez Silva Santos Costa "
        "Oliveira Haddad Khoury Aziz Rahman Chowdhury O'Brien").split()
AR_FIRST = "فرح عمر مريم يوسف نور كريم ليلى حسن سلمى محمد منى طارق هنا خالد دينا".split()
AR_LAST = "أحمد محمود إبراهيم سليمان فاروق ناصر صالح منصور حداد عزيز".split()
CITY = ("Cairo Giza Alexandria Amman Dubai Riyadh London Manchester Leeds Austin Chicago Seattle Toronto "
        "Berlin Munich Paris Lyon Madrid Barcelona Rome Milan Mumbai Delhi Tokyo Osaka Seoul Sydney "
        "Melbourne Lagos Nairobi Accra Lisbon Warsaw Prague Istanbul Casablanca Tunis Doha Beirut "
        "Denver Boston Phoenix").split()
AR_CITY = "القاهرة الجيزة الإسكندرية عمّان دبي الرياض بيروت تونس".split()
STATE = ["Texas", "California", "Ontario", "Bavaria", "Cairo Governorate", "Giza Governorate",
         "New South Wales", "Lombardy", "Catalonia", "Maharashtra", "Florida", "Washington",
         "Quebec", "Colorado", "Ohio", "Alexandria Governorate"]
COUNTRY = ["Egypt", "Jordan", "United States", "United Kingdom", "Canada", "Germany", "France", "Spain",
           "Italy", "India", "Japan", "South Korea", "Australia", "Nigeria", "Kenya", "Ghana", "Portugal",
           "Poland", "Turkey", "Morocco", "Tunisia", "Qatar", "Saudi Arabia", "Lebanon", "Brazil", "Mexico"]
STREET_US = ["Birch Lane", "Maple Avenue", "Oak Street", "Cedar Road", "Elm Drive", "Sunset Boulevard",
             "Park Lane", "Highland Court", "River Road", "Lakeview Terrace", "Willow Way", "Pine Street"]
STREET_EG = ["El-Tahrir St", "Pyramids Rd", "Nile Corniche", "Mohandessin St", "Gameat El Dowal St",
             "Road 9", "El-Nasr St", "26th of July St"]
DISTRICT_EG = ["Dokki", "Maadi", "Zamalek", "Nasr City", "Mohandessin", "Heliopolis", "6th of October",
               "Agouza", "New Cairo"]
STREET_DE = ["Hauptstraße", "Bahnhofstraße", "Gartenweg", "Schillerstraße", "Lindenallee"]
STREET_FR = ["rue de la Paix", "avenue Victor Hugo", "rue des Lilas", "boulevard Voltaire"]
STREET_ES = ["Calle Mayor", "Avenida de la Constitución", "Calle del Sol", "Paseo de Gracia"]
ORGS = ["Cedar Ridge Logistics", "Saffron Analytics", "Pinecrest Dental", "Nile Delta Foods", "Atlas Roofing",
        "Brightwave Media", "Copperfield Insurance", "Harbor & Vine Cafe", "Tidewater Credit Union",
        "Meridian Clinic", "Skyline Robotics", "Oakmont Legal", "Rowan & Pike", "Zephyr Airlines",
        "Granite State Bank", "Lotus Pharmacy"]
WORDS = ("falcon river amber maple orbit velvet cactus harbor lantern copper meadow thunder pebble "
         "saffron glacier compass walnut ember willow quartz tundra marble ranger summit pepper "
         "violet anchor canyon dolphin ginger horizon jasmine kettle lagoon mosaic nectar").split()
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
PWD_WORDS = ["Sunshine", "Dragon", "Winter", "Pharaoh", "Password", "Welcome", "Monkey", "Football",
             "Summer", "Letmein", "Qwerty", "Admin", "Cairo", "Falcon"]

POOLS_RAW = dict(first=FIRST, last=LAST, ar_first=AR_FIRST, ar_last=AR_LAST, city=CITY, ar_city=AR_CITY,
                 state=STATE, country=COUNTRY, street_us=STREET_US, street_eg=STREET_EG,
                 district=DISTRICT_EG, street_de=STREET_DE, street_fr=STREET_FR, street_es=STREET_ES,
                 word=WORDS, pwdw=PWD_WORDS)


def make_pools(dev: bool) -> dict:
    return {k: [x for i, x in enumerate(v) if (i % 5 == 0) == dev] or list(v)
            for k, v in POOLS_RAW.items()}


# --------------------------------------------------------------------------
# Value generators:  f(rng, P) -> str
# --------------------------------------------------------------------------
def c(rng, P, k):
    return rng.choice(P[k])


def g_person(rng, P):
    f, l = c(rng, P, "first"), c(rng, P, "last")
    r = rng.random()
    if r < 0.60:
        return f"{f} {l}"
    if r < 0.75:   # the base model deliberately leaves titles out of the name -> so do we
        return [(rng.choice(["Dr.", "Mr.", "Ms.", "Mrs.", "Prof."]) + " ", None), (f"{f} {l}", "person")]
    if r < 0.85:
        return f"{l}, {f}"
    if r < 0.93:
        return f"{f} {rng.choice('ABCDEFGHJKLMNPRSTW')}. {l}"
    return f"{f} {l}".upper() if rng.random() < 0.5 else f"{f} {l}".lower()


def g_first(rng, P):
    return c(rng, P, "first")


def g_last(rng, P):
    return c(rng, P, "last")


def g_ar_person(rng, P):
    return f"{c(rng, P, 'ar_first')} {c(rng, P, 'ar_last')}"


def _date(rng, y0, y1):
    y = rng.randint(y0, y1)
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    return y, m, d


def fmt_date(rng, y, m, d):
    k = rng.randint(0, 5)
    if k == 0:
        return f"{d:02d}/{m:02d}/{y}"
    if k == 1:
        return f"{y}-{m:02d}-{d:02d}"
    if k == 2:
        return f"{MONTHS[m-1]} {d}, {y}"
    if k == 3:
        return f"{d} {MONTHS[m-1]} {y}"
    if k == 4:
        return f"{d:02d}.{m:02d}.{y}"
    return f"{d}-{MONTHS[m-1][:3]}-{y}"


def g_dob(rng, P):
    return fmt_date(rng, *_date(rng, 1938, 2009))


def g_sdate(rng, P):
    return fmt_date(rng, *_date(rng, 2018, 2026))


def g_bdate(rng, P):  # benign date, NOT a label
    return fmt_date(rng, *_date(rng, 2023, 2027))


def g_city(rng, P):
    return c(rng, P, "city")


def g_ar_city(rng, P):
    return c(rng, P, "ar_city")


def g_state(rng, P):
    return c(rng, P, "state")


def g_country(rng, P):
    return c(rng, P, "country")


def g_zip(rng, P):
    k = rng.randint(0, 2)
    if k == 0:
        return f"{rng.randint(10000, 99999)}"
    if k == 1:
        return f"{rng.choice('MBLNSEW')}{rng.randint(1, 20)} {rng.randint(1, 9)}{rng.choice('ABDEFGHJLNPRSTUWXYZ')}{rng.choice('ABDEFGHJLNPRSTUWXYZ')}"
    return f"{rng.randint(1000, 9999)}"


def g_addr(rng, P):
    n = rng.randint(1, 999)
    k = rng.choice(["us", "us", "uk", "eg", "de", "fr", "es"])
    city = c(rng, P, "city")
    if k == "us":
        apt = f", Apt {rng.randint(1, 40)}{rng.choice('ABCD')}" if rng.random() < 0.3 else ""
        return f"{n} {c(rng, P, 'street_us')}{apt}, {city}, {rng.choice(['TX','CA','NY','IL','WA','CO','FL'])} {rng.randint(10000, 99999)}"
    if k == "uk":
        return f"{n} {c(rng, P, 'street_us')}, {city} {g_zip_uk(rng)}"
    if k == "eg":
        return f"{n} {c(rng, P, 'street_eg')}, {c(rng, P, 'district')}, {rng.choice(['Cairo', 'Giza', 'Alexandria'])}"
    if k == "de":
        return f"{c(rng, P, 'street_de')} {n}, {rng.randint(10000, 99999)} {city}"
    if k == "fr":
        return f"{n} {c(rng, P, 'street_fr')}, {rng.randint(10000, 99999)} {city}"
    return f"{c(rng, P, 'street_es')} {n}, {rng.randint(10000, 52999)} {city}"


def g_zip_uk(rng):
    return f"{rng.choice('MBLNSEW')}{rng.randint(1, 20)} {rng.randint(1, 9)}{rng.choice('ABDEFGHJLNPRSTUWXYZ')}{rng.choice('ABDEFGHJLNPRSTUWXYZ')}"


def g_user(rng, P):
    f, l = c(rng, P, "first").lower(), c(rng, P, "last").lower().replace("'", "")
    k = rng.randint(0, 5)
    if k == 0:
        return f"{f[0]}{l}{rng.randint(1, 99)}"
    if k == 1:
        return f"{f}_{l[0]}"
    if k == 2:
        return f"{f}.{l[0]}{rng.randint(80, 99)}"
    if k == 3:
        return f"{c(rng, P, 'word')}_{rng.choice(['rider', 'dev', 'fan', 'ninja', 'pilot'])}{rng.randint(1, 99)}"
    if k == 4:
        return f"{f}{l}{rng.randint(100, 999)}"
    return f"{l}.{f}"


def g_pwd(rng, P):
    k = rng.randint(0, 4)
    sym = rng.choice("!@#$%&*")
    if k == 0:
        return f"{c(rng, P, 'pwdw')}{rng.randint(1, 2026)}{sym}"
    if k == 1:
        return rng.choice(["hunter2", "letmein123", "qwerty123", "iloveyou1", "admin123", "p4ssw0rd"]) + (sym if rng.random() < .4 else "")
    if k == 2:
        n = rng.randint(10, 14)
        return "".join(rng.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%") for _ in range(n))
    if k == 3:
        return f"{c(rng, P, 'pwdw').lower()}{sym}{c(rng, P, 'pwdw')}{rng.randint(10, 99)}"
    return f"{c(rng, P, 'pwdw')}@{rng.randint(1990, 2026)}"


def g_pwd_long(rng, P):
    k = rng.randint(0, 2)
    if k == 0:
        n = rng.randint(16, 26)
        return "".join(rng.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%^&*") for _ in range(n))
    if k == 1:
        return "-".join(c(rng, P, "pwdw") for _ in range(rng.randint(3, 4))) + f"{rng.randint(1, 99)}{rng.choice('!@#$')}"
    return f"{c(rng, P, 'pwdw')}{c(rng, P, 'word').capitalize()}{rng.randint(100, 9999)}{rng.choice('!@#$%')}{rng.choice('!@#$%')}"


def g_secret(rng, P):
    k = rng.randint(0, 3)
    if k == 0:
        return "-".join(rng.choice(P["word"]) for _ in range(rng.randint(3, 4)))
    if k == 1:
        return " ".join(rng.choice(P["word"]) for _ in range(rng.choice([3, 4])))
    if k == 2:
        return rng.choice(P["word"]) + str(rng.randint(10, 9999))
    return " ".join(rng.choice(P["word"]) for _ in range(12))


def g_natid_eg(rng, P):
    y, m, d = _date(rng, 1950, 2005)
    cen = "2" if y < 2000 else "3"
    return f"{cen}{y % 100:02d}{m:02d}{d:02d}{rng.randint(1, 35):02d}{rng.randint(0, 9999):04d}{rng.randint(0, 9)}"


def g_govid(rng, P):
    k = rng.randint(0, 5)
    if k == 0:
        return g_natid_eg(rng, P)
    if k == 1:
        return f"{rng.choice('ABCEGHJKLMNPRSTWXYZ')}{rng.choice('ABCEGHJKLMNPRSTWXYZ')}{rng.randint(100000, 999999)}{rng.choice('ABCD')}"
    if k == 2:
        return f"{rng.randint(1000, 9999)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"
    if k == 3:
        return f"{rng.randint(10000000, 99999999)}{rng.choice('TRWAGMYFPDXBNJZSQVHLCKE')}"
    if k == 4:
        return f"{rng.randint(100, 999)}.{rng.randint(100, 999)}.{rng.randint(100, 999)}-{rng.randint(10, 99)}"
    return f"{rng.choice('LMNPRT')}{rng.randint(10, 99)}{rng.choice('CFGHJKLMNPRTVWXYZ')}{rng.randint(10, 99)}{rng.choice('CFGHJKLMNPRTVWXYZ')}{rng.randint(1, 9)}"


def g_passport(rng, P):
    k = rng.randint(0, 3)
    if k == 0:
        return f"A{rng.randint(10000000, 99999999)}"
    if k == 1:
        return f"{rng.randint(100000000, 999999999)}"
    if k == 2:
        return f"{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.randint(1000000, 9999999)}"
    return f"{rng.choice('CDEFGHJKLMNPRTVWXYZ')}{rng.randint(1000000, 9999999)}"


def g_dl(rng, P):
    k = rng.randint(0, 3)
    if k == 0:
        return f"D{rng.randint(1000000, 9999999)}"
    if k == 1:
        return f"DL-{rng.randint(10000000, 99999999)}"
    if k == 2:
        return f"{rng.choice('ABCDEFGHJKLMNPRSTW')}{rng.randint(100, 999)}-{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(100, 999)}-{rng.randint(0, 9)}"
    return f"{rng.randint(10000000, 99999999)}"


# unlabeled fillers (realistic context, NOT Tier 2 labels)
def g_email(rng, P):
    return f"{c(rng, P, 'first').lower()}.{c(rng, P, 'last').lower().replace(chr(39), '')}@{rng.choice(['example.com', 'mail.org', 'corp-mail.net', 'acme.io'])}"


def g_phone(rng, P):
    return rng.choice([f"+1 415-555-{rng.randint(100, 199):04d}", f"+20 100 {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
                       f"+44 20 7946 {rng.randint(1000, 9999)}"])


def g_org(rng, P):
    return rng.choice(ORGS)


def g_amount(rng, P):
    return f"${rng.randint(50, 90000):,}"


def g_ip(rng, P):
    return f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def g_host(rng, P):
    return rng.choice(["prod-db-01.internal", "build-server", "bastion.corp.local", "10.0.0.5"])


def g_empid(rng, P):
    return f"EMP-{rng.randint(10000, 99999)}"


def g_ordid(rng, P):
    return f"ORD-{rng.randint(10000000, 99999999)}"


def g_trk(rng, P):  # passport lookalike, NOT a passport
    return f"{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.randint(10000000, 99999999)}"


def g_trk14(rng, P):  # 14-digit lookalike of Egyptian national id, NOT a gov id
    return "".join(str(rng.randint(0, 9)) for _ in range(14))


def g_ver(rng, P):
    return f"v{rng.randint(1, 9)}.{rng.randint(0, 20)}.{rng.randint(0, 9)}"


def g_inv(rng, P):
    return f"INV-{rng.randint(2022, 2026)}-{rng.randint(100, 9999):04d}"


def g_room(rng, P):
    return f"{rng.choice('ABC')}-{rng.randint(100, 499)}"


# tag -> (generator, gliner label or None).  A label starting with "~" is a Tier-1 type that appears as
# realistic context: never a GLiNER training label, and written to the test file as tier=3 = "ignore in scoring"
# (we neither reward nor penalise detecting it).
SEED_WORDS = ("abandon able acid actor adapt agent alarm album alert alpha anchor angle ankle apple arena armor "
              "arrow aspect atlas autumn avocado bamboo banner barrel basket beach bench bicycle blanket "
              "blossom border bottle bracket bridge bronze bubble cabin cable camera candle carbon carpet "
              "castle cattle ceiling cement chapter cherry circle cliff clock cloud clutch coconut comet "
              "cotton couch crater cricket crystal cushion dawn debate decade desert diamond dinner doctor "
              "domain dragon drift eagle echo elbow engine fabric fiber flame forest fossil fountain "
              "galaxy garden gesture glove gravity guitar hammer harvest helmet hobby island jacket jungle "
              "kitchen ladder laptop lecture lemon lizard magnet mirror monitor mountain napkin oasis "
              "orchard oyster paddle palace parade pencil pigeon planet pocket pyramid rabbit rhythm "
              "ribbon rocket saddle salmon scatter shadow silver slogan spider spirit statue sunset "
              "tablet temple ticket timber tomato tunnel umbrella valley village walnut window zebra").split()


SEED_WORDS += ("account actress advice agree airport almond amateur ancient angry apart arch artist atom "
               "bacon badge basic bean beyond bicycle blouse boil bonus bread breeze brick broom buddy "
               "cabbage cage canal canvas cargo carry catalog chair chalk cheese chief chimney choice "
               "cinnamon claim clay clever clinic coach cousin craft cream crowd cruise daring decorate "
               "deer deposit dice dizzy donkey drama dune elder elephant empty enemy erupt evoke exhibit "
               "faculty fence festival fiction flavor flock fork frozen funny future gadget garlic giant "
               "goose gospel grain grape grid guard hamster hazard hello herb hockey honey humble hybrid "
               "icon idle iron jelly jewel juice kangaroo kiwi knife lava leader limb liquid lobster "
               "lunar mango maple marble mixture muffin museum noble noodle nuclear oak olive onion "
               "orange pave peanut pelican piano pilot pizza plastic polar pottery pretty pupil puzzle "
               "radar raven rebel rifle riot robot salad scrub sheriff shield shrimp skate snack soda "
               "sponge squirrel stable sugar swallow tackle theory tiger toast turtle upgrade vanish "
               "velvet vendor volcano warrior whale wheat wisdom wolf yellow zero zone").split()


def g_seed(rng, P):
    """12 or 24 DISTINCT words from a larger vocabulary (BIP39-like), space or dash separated."""
    n = rng.choice([12, 12, 15, 18, 24])
    return rng.choice([" ", " ", "-"]).join(rng.sample(SEED_WORDS, n))


def g_gtok(rng, P):
    """grouped access token: wifi key / activation key / invite code style"""
    k = rng.randint(0, 2)
    alpha = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    if k == 0:
        return "-".join("".join(rng.choice(alpha) for _ in range(rng.randint(4, 6))) for _ in range(rng.randint(3, 5)))
    if k == 1:
        a = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "-".join("".join(rng.choice(a) for _ in range(5)) for _ in range(rng.randint(4, 5)))
    return "".join(rng.choice(alpha) for _ in range(rng.randint(14, 22)))


def g_sku(rng, P):   # product / order reference look-alike: NOT a secret
    return rng.choice([f"SKU-{rng.randint(10000, 99999)}-{rng.choice('ABCD')}", f"PN {rng.randint(1000, 9999)}-{rng.randint(100, 999)}",
                       f"REF-{rng.randint(100000, 999999)}", f"TKT-{rng.randint(1000, 99999)}", f"BLD-{rng.randint(100, 9999)}-{rng.choice('xyz')}"])


def g_hostn(rng, P):
    """hosts that are NOT e-mail shaped (no letters-only TLD), so  user@host  splits into separate tokens"""
    return rng.choice(["build-server", "db01", "localhost", "bastion", "prod-db-01", "10.0.0.5", "192.168.1.20", "gw-2", "jump01"])


def g_atuser(rng, P):
    """@handle: GLiNER2 keeps '@name' as ONE token, so the span must include the '@'"""
    return "@" + g_user(rng, P).replace(".", "_")


def g_tool(rng, P):
    return rng.choice(["git", "docker", "kubectl", "terraform", "npm", "pip", "vault", "os.environ", "module.py",
                       "foo.bar.baz", "nginx", "redis", "ansible", "make", "curl"])


def g_generic(rng, P):
    return rng.choice(["team", "security", "platform", "finance", "support", "vault", "wiki", "gateway"])


def g_shortdate(rng, P):
    return rng.choice([f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}", f"{rng.randint(1, 12)}/{rng.randint(1, 28)}",
                       f"{rng.randint(2025, 2027)}-12-31"])


# --- technical look-alikes (never PII): used only in hard negatives
def g_hash(rng, P):
    n = rng.choice([8, 12, 32, 40, 64])
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def g_uuid(rng, P):
    h = lambda n: "".join(rng.choice("0123456789abcdef") for _ in range(n))
    return f"{h(8)}-{h(4)}-4{h(3)}-a{h(3)}-{h(12)}"


def g_hexc(rng, P):
    return "#" + "".join(rng.choice("0123456789ABCDEF") for _ in range(6))


def g_tsmp(rng, P):
    k = rng.randint(0, 2)
    if k == 0:
        return f"{rng.randint(0, 59)}m{rng.randint(0, 59):02d}s"
    if k == 1:
        return f"{rng.randint(0, 2):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
    return f"{rng.randint(2023, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00Z"


def g_coord(rng, P):
    return f"{rng.uniform(-90, 90):.4f}"


def g_num(rng, P):
    return rng.choice(["3.14159", "2.71828", "1.41421", "0.5772", f"{rng.uniform(0, 100):.3f}"])


def g_int(rng, P):
    return str(rng.randint(2, 99999))


def g_branch(rng, P):
    return rng.choice(["main", "develop", "master", f"feature/{rng.choice(P['word'])}-fix",
                       f"release/{rng.randint(1, 9)}.{rng.randint(0, 9)}", f"hotfix/{rng.choice(P['word'])}"])


def g_isbn(rng, P):
    return f"ISBN 978-{rng.randint(0, 9)}-{rng.randint(10, 99)}-{rng.randint(100000, 999999)}-{rng.randint(0, 9)}"


def g_envvar(rng, P):
    return rng.choice(["DB_PASSWORD", "API_SECRET", "SECRET_KEY", "AWS_SECRET_ACCESS_KEY", "SMTP_PASS",
                       "ADMIN_PASSWORD", "JWT_SECRET", "REDIS_PASSWORD", "USERNAME", "DB_USER"])


def g_path(rng, P):
    return rng.choice(["/usr/local/bin/python3", "C:\\Users\\Public\\app.exe", "src/utils/helpers.py",
                       "/var/log/app/error.log", "~/projects/demo/config.yaml", "./node_modules/.bin/eslint"])


def g_loop(rng, P):
    return rng.choice(["127.0.0.1", "0.0.0.0", "localhost:8080", "127.0.0.1:3000", "localhost"])


TAGS = {
    "person": (g_person, "person"), "person2": (g_person, "person"),
    "first": (g_first, "person"), "first2": (g_first, "person"), "last": (g_last, "person"),
    "ar_person": (g_ar_person, "person"),
    "dob": (g_dob, "date_of_birth"), "sdate": (g_sdate, "sensitive_date"),
    "addr": (g_addr, "address"),
    "city": (g_city, "city"), "city2": (g_city, "city"), "ar_city": (g_ar_city, "city"),
    "state": (g_state, "state_or_region"), "state2": (g_state, "state_or_region"), "country": (g_country, "country"),
    "zip": (g_zip, "postal_code"),
    "user": (g_user, "username"), "pwd": (g_pwd, "password"), "secret": (g_secret, "secret"),
    "natid": (g_natid_eg, "government_id"), "govid": (g_govid, "government_id"),
    "passport": (g_passport, "passport_number"), "dl": (g_dl, "drivers_license_number"),
    "pwdl": (g_pwd_long, "password"),
    # fillers
    "email": (g_email, "~EMAIL"), "phone": (g_phone, "phone_number"), "org": (g_org, "organization"), "amount": (g_amount, None),
    "ip": (g_ip, "~IP_ADDRESS"), "host": (g_host, "~INTERNAL_HOSTNAME"), "empid": (g_empid, None), "ordid": (g_ordid, None),
    "trk": (g_trk, None), "trk14": (g_trk14, None), "ver": (g_ver, None), "inv": (g_inv, None),
    "room": (g_room, None), "bdate": (g_bdate, None),
    # technical look-alikes (unlabeled)
    "hash": (g_hash, None), "uuid": (g_uuid, None), "hexc": (g_hexc, None), "tsmp": (g_tsmp, None),
    "coord": (g_coord, None), "coord2": (g_coord, None), "num": (g_num, None), "int": (g_int, None),
    "branch": (g_branch, None), "isbn": (g_isbn, None), "envvar": (g_envvar, None), "path": (g_path, None),
    "loop": (g_loop, None), "bdate2": (g_bdate, None),
    "seed": (g_seed, "secret"), "tool": (g_tool, None), "generic": (g_generic, None),
    "sdt": (g_shortdate, None),
    "gtok": (g_gtok, "secret"), "sku": (g_sku, None),
    "hostn": (g_hostn, None), "atuser": (g_atuser, "username"),
}

# --------------------------------------------------------------------------
# Templates: category -> [(template, labels_that_must_be_empty)]
# --------------------------------------------------------------------------
POS = {
    "person": [
        ("My name is {person} and I need help with my account.", ()),
        ("Hi {first}, thanks for reaching out. I've looped in {person} from billing.", ()),
        ("Please send the signed contract to {person} by Friday.", ()),
        ("Dear {person},\n\nYour appointment is confirmed for next week.", ()),
        ("Can you summarize the complaint filed by {person} against {org}?", ()),
        ("From: {person} <{email}>\nSubject: Re: {inv}", ()),
        ("Interview notes for candidate {person}: strong Python, weak on SQL.", ()),
        ("{person} said she would call back at {phone}.", ()),
        ("{first} is out sick today, ping {first2} instead.", ()),
        ("Employee: {person}\nID: {empid}\nManager: {person2}", ()),
        ("log: user {person} logged in from {ip}", ()),
        ("Summarize the main themes in {person}'s latest interview.", ()),
        ("Thanks,\n{person}\n{org}", ()),
        ("i'm {first} btw, nice to meet u", ()),
        ("Reference letter for {person}, who worked under me at {org} for three years.", ()),
        ("Mr. {last} will be joining the call at 3pm.", ()),
        ("{first}: ok I'll send it tonight\n{first2}: thanks!", ()),
        ("[{first}] hey, are you around?", ()),
        ("{first} wrote:\n> can you check this?", ()),
        ("Hello, I'm {person} from {org} in {city}. Reach me at {email}.", ()),
        ("Meeting notes: {person} ({org}) will own the rollout; {person2} reviews.", ()),
        ("cc: {person}; {person2}", ()),
        ("Regards,\n{person}\n{org}\n{phone}", ()),
    ],
    "dob": [
        ("DOB: {dob}", ()),
        ("I was born on {dob}, can you calculate my age?", ()),
        ("Date of birth: {dob}\nPlace of birth: {city}", ()),
        ("Patient {person} (born {dob}) presented with chest pain.", ()),
        ("My son was born on {dob} and needs a passport.", ()),
        ("Please verify identity: {person}, born {dob}.", ()),
        ("Birthdate - {dob}", ()),
    ],
    "sdate": [
        ("The patient was admitted on {sdate} after a fall.", ("date_of_birth",)),
        ("Diagnosis was confirmed on {sdate}.", ("date_of_birth",)),
        ("Court hearing scheduled for {sdate} regarding the custody case.", ("date_of_birth",)),
        ("She filed for bankruptcy on {sdate}.", ("date_of_birth",)),
        ("Surgery date: {sdate}", ("date_of_birth",)),
        ("He was terminated on {sdate} following the investigation.", ("date_of_birth",)),
        ("Date of the incident: {sdate}", ("date_of_birth",)),
    ],
    "address": [
        ("Ship it to {addr}.", ()),
        ("My home address is {addr}.", ()),
        ("Billing address: {addr}", ()),
        ("Send the package to {person}, {addr}", ()),
        ("The property at {addr} is listed at {amount}.", ()),
        ("Return address:\n{person}\n{addr}", ()),
        ("I just moved to {addr} and need to update my records.", ()),
    ],
    "location": [
        ("I live in {city} and work remotely.", ("address",)),
        ("Our new office opens in {city}, {country} next spring.", ("address",)),
        ("What should I pack for a week in {city}?", ("address",)),
        ("Zip code {zip}, is it in {state}?", ()),
        ("He moved from {city}, {state} to {country}.", ()),
        ("Flights from {city} to {city2} are cheaper on Tuesdays.", ()),
        ("Delivery to {city} {zip} usually takes two days.", ()),
        ("My zip is {zip} and I'm in {state}.", ()),
        ("The conference will be held in {country}.", ()),
        ("Is {zip} the right postcode for {city}?", ()),
        ("Postal code: {zip}", ()),
        ("Licensed to practice in {state}.", ()),
        ("She moved to {state} last year and works in {city}.", ()),
        ("Sales tax in {state} is different from {state2}.", ()),
        ("Our regional office for {state}, {country} is closing.", ()),
        ("I'm currently in {state} until Friday.", ()),
    ],
    "username": [
        ("My username is {user} but I forgot the password.", ()),
        ("Login: {user}", ()),
        ("Check my profile at github.com/{user}", ()),
        ("{atuser} please review the PR", ()),
        ("ssh {user}@{host}", ()),
        ("user={user}", ()),
        ("Instagram: {atuser}", ()),
    ],
    "password": [
        ("my password is {pwd}", ()),
        ("password: {pwd}", ()),
        ("Use the wifi password {pwd} to connect.", ()),
        ("pwd={pwd} # temporary, rotate later", ()),
        ("The admin password was changed to {pwd} yesterday.", ()),
        ("login: {user} / {pwd}", ()),
        ("Here are the creds you asked for. user: {user} pass: {pwd}", ()),
        ("the new wifi key is {pwdl}, don't post it anywhere", ()),
        ("Temporary password: {pwdl} (change it on first login)", ()),
        ("db password = {pwdl}", ()),
        ("La contraseña del wifi es {pwdl}.", ()),
        ("Le mot de passe temporaire est {pwdl}, changez-le vite.", ()),
        ("Das Passwort für den Server lautet {pwdl}.", ()),
        ("كلمة سر الواي فاي هي {pwdl}", ()),
        ("Your one-off login: {user} / {pwdl}", ()),
        ("admin pass is {pwd}", ()),
    ],
    "secret": [
        ("the secret phrase is {secret}", ()),
        ("Recovery passphrase: {secret}", ()),
        ("Safe word: {secret}", ()),
        ("My security answer is {secret}", ()),
        ("seed phrase: {secret}", ()),
        ("Don't share this: the vault passphrase is {secret}", ()),
        ("La frase secreta es {secret}, no la compartas.", ()),
        ("La phrase secrète est {secret}.", ()),
        ("Die Geheimphrase lautet {secret}.", ()),
        ("العبارة السرية هي {secret}", ()),
        ("backup code words: {secret}", ()),
        ("My mnemonic: {secret}. Please store it safely.", ()),
    ],
    "govid": [
        ("National ID: {natid}", ()),
        ("My national ID number is {natid}.", ()),
        ("ID card no. {govid}", ()),
        ("Please upload a copy of your ID ({govid}) for verification.", ()),
        ("{person}, national id {natid}, resident of {city}", ()),
    ],
    "passport": [
        ("Passport number: {passport}", ()),
        ("My passport {passport} expires next year.", ()),
        ("Booking for {person}, passport no. {passport}.", ()),
        ("passport # {passport}", ()),
    ],
    "dl": [
        ("Driver's license: {dl}", ()),
        ("DL# {dl} issued in {state}", ()),
        ("Licence number {dl}, please verify.", ()),
        ("{person} presented driving license {dl} at the desk.", ()),
    ],
    "multi": [
        ("KYC form\nName: {person}\nDOB: {dob}\nAddress: {addr}\nNational ID: {natid}", ()),
        ("Patient: {person}\nDOB: {dob}\nAdmitted: {sdate}\nContact: {phone}", ()),
        ("name,dob,city\n{person},{dob},{city}\n{person2},{dob},{city2}", ()),
        ('{"name": "{person}", "dob": "{dob}", "city": "{city}", "country": "{country}"}', ()),
        ("Hi, this is {person}. I live at {addr}. My passport is {passport}, can you check my visa?", ()),
        ("Support ticket #{ordid}\nCustomer: {person}\nEmail: {email}\nUsername: {user}\nIssue: cannot log in, password {pwd} rejected", ()),
        ("HR record: {person}, {dob}, {addr}, driver's license {dl}", ()),
        ("Lease agreement between {person} (national ID {natid}) and {person2}, for the property at {addr}.", ()),
        ("Dear {person},\nYour account {user} was accessed from {city}, {country} on {sdate}.", ()),
        ("def create_user():\n    name = \"{person}\"\n    pwd = \"{pwd}\"\n    city = \"{city}\"", ()),
        ("Deposition of {person}, born {dob}, residing at {addr}. Hearing set for {sdate}.", ()),
        ("{first}: what's the wifi password again?\n{first2}: it's {pwd}", ()),
        ("Resume\n{person}\n{addr}\n{phone} | {email}\nDOB: {dob}", ()),
        ("Visa application for {person}. Passport {passport}, national ID {natid}, born {dob} in {city}, {country}.", ()),
    ],
    "lang": [  # Arabic / Spanish / French / German
        ("اسمي {ar_person} وأسكن في {ar_city}.", ()),
        ("تاريخ ميلادي {dob}", ()),
        ("الرقم القومي: {natid}", ()),
        ("اسم المستخدم: {user}", ()),
        ("كلمة المرور هي {pwd}", ()),
        ("العميل {ar_person} طلب تغيير عنوانه إلى {addr}.", ()),
        ("من فضلك أرسل الفاتورة إلى {ar_person} في {ar_city}.", ()),
        ("رقم جواز السفر: {passport}", ()),
        ("Me llamo {person} y nací el {dob} en {city}.", ()),
        ("Mi contraseña es {pwd}, no se la digas a nadie.", ()),
        ("Je m'appelle {person}, j'habite au {addr}.", ()),
        ("Mon mot de passe est {pwd}.", ()),
        ("Ich heiße {person} und wohne in der {addr}.", ()),
        ("Mein Passwort lautet {pwd}.", ()),
    ],
}

NEG_TECH = [
    ("git checkout {branch} && git pull", ("address", "city", "username", "person")),
    ("Skip to {tsmp} in the recording.", ("sensitive_date", "date_of_birth", "city")),
    ("Commit {hash} fixed the null pointer bug.", ("drivers_license_number", "government_id", "secret", "password")),
    ("sha256: {hash}", ("drivers_license_number", "government_id", "secret", "password")),
    ("Request id: {uuid}", ("government_id", "secret", "password", "drivers_license_number")),
    ("background: {hexc}; color: #fff;", ("secret", "password")),
    ("Color tokens: primary {hexc}, accent {hexc}", ("secret", "password")),
    ("The book ({isbn}) is out of print.", ("government_id", "passport_number")),
    ("export {envvar}=$VALUE", ("password", "secret")),
    ("os.environ['{envvar}']", ("password", "secret", "username")),
    ("const key = process.env.{envvar};", ("secret", "password")),
    ("Server is listening on {loop}", ("city", "address")),
    ("curl http://{loop}/health", ("city", "address")),
    ("Pi is approximately {num}.", ("government_id", "passport_number")),
    ("Coordinates: lat {coord}, lon {coord2}", ("city", "address", "government_id")),
    ("cd {path} && ls", ("address", "username")),
    ("File saved to {path}", ("address", "username")),
    ("Set password to <YOUR_PASSWORD> in the config.", ("password", "secret")),
    ('api_key = "YOUR_API_KEY_HERE"', ("secret", "password")),
    ("password = None  # TODO load from vault", ("password", "secret")),
    ('Traceback (most recent call last):\n  File "{path}", line {int}, in <module>\nKeyError: \'password\'', ("password", "secret", "username")),
    ("SELECT * FROM users WHERE id = {int}", ("username", "person")),
    ("Error 404: user not found", ("username",)),
    ("Updated {ver} on {bdate}. See branch {branch}.", ("sensitive_date", "date_of_birth")),
    ("The checksum {hash} did not match.", ("drivers_license_number", "government_id", "secret")),
    ("Docker image: registry.example.com/app:{ver}", ("city", "address")),
    ("Use the 'username' field and the 'password' field in the form.", ("username", "password")),
    ("Dataset has {int} rows and columns: name, address, city, zip.", ("person", "address", "city", "postal_code")),
    ("The meeting is on {bdate}.", ("sensitive_date", "date_of_birth")),
    ("Lorem ipsum dolor sit amet, consectetur adipiscing elit.", ("person", "address")),
    ("Rate limit: {int} requests per minute from {loop}", ("city", "address")),
    ("Ctrl+C to quit; logs at {path}; build {hash}", ("secret", "password", "address")),
]

NEG = {
    "neg_tech": NEG_TECH,
    "neg_person": [
        ("Please mark the task as done. Will you join the call?", ("person",)),
        ("The hunter in the story tracks a bear through the forest.", ("person",)),
        ("The {country=Jordan} River flows into the Dead Sea.", ("person", "city")),
        ("Rose bushes should be pruned in early spring.", ("person",)),
        ("Python and Mercury are both on my reading list.", ("person",)),
        ("{org} and {organization=Apple} both announced earnings this week.", ("person",)),
        ("The grace period is 30 days; the bill is due on the 5th.", ("person", "sensitive_date")),
        ("The capital of {country=Jordan} is {city=Amman}.", ("person",)),
        ("{city=Austin} is growing fast, but {city=Houston} is still bigger.", ("person",)),
        ("{city=Paris} is lovely in spring, unlike {city=Seattle} in winter.", ("person",)),
    ],
    "neg_code_ui": [
        ("first_name = request.form['first_name']\nlast_name = request.form['last_name']", ("person",)),
        ("SELECT first_name, last_name, date_of_birth FROM users WHERE username = ?", ("person", "date_of_birth", "username")),
        ("def check_password(username, password):\n    return hash(password) == db[username]", ("username", "password")),
        ("Enter your username and password to continue.", ("username", "password")),
        ("Forgot your password? Reset it here.", ("password",)),
        ("Date of birth (DD/MM/YYYY)", ("date_of_birth",)),
        ("Password must be at least 8 characters and include a number.", ("password",)),
        ("Your passport must be valid for six months beyond your travel dates.", ("passport_number",)),
        ("Please upload a photo of your national ID.", ("government_id",)),
        ("The users table has columns: id, username, password_hash, created_at.", ("username", "password")),
        ("Street address, city, state, and zip code are required fields.", ("address", "city", "postal_code")),
        ("Set the environment variable DB_PASSWORD before starting the container.", ("password",)),
    ],
    "neg_lookalike": [
        ("Order {ordid} shipped on {bdate}.", ("passport_number", "government_id", "sensitive_date", "date_of_birth")),
        ("Tracking number {trk} is in transit.", ("passport_number",)),
        ("Parcel tracking {trk14} was delivered.", ("government_id",)),
        ("{ver} was released on {bdate}.", ("sensitive_date", "date_of_birth")),
        ("Invoice {inv} is due on {bdate}.", ("sensitive_date", "date_of_birth")),
        ("Team standup is Monday at 10am in room {room}.", ("sensitive_date", "date_of_birth")),
        ("Q3 results will be published on {bdate}.", ("sensitive_date", "date_of_birth")),
        ("Case reference {trk} was escalated to tier 2.", ("passport_number", "government_id")),
    ],
    "neg_placeholder": [
        ("password: ********", ("password",)),
        ("username: <your-username>", ("username",)),
        ("password=<YOUR_PASSWORD>", ("password",)),
        ("Dear [Customer Name],\nThank you for your order.", ("person",)),
        ("My name is [NAME] and I live in [CITY].", ("person", "city")),
        ("user: example_user pass: changeme", ("username", "password")),
    ],
    "neg_plain": [
        ("Hello world! This is a test.", ("person", "address", "password")),
        ("How do I center a div in CSS?", ("person", "address", "username")),
        ("Explain how photosynthesis works.", ("person", "city", "country")),
        ("Write a haiku about autumn rain.", ("person", "city")),
        ("What's the difference between TCP and UDP?", ("person", "username")),
    ],
}

# Added to TRAINING only (never to the dev/test split), worded differently from the held-out templates so the
# test still measures generalisation. They target failures seen in the baseline on real hold-out + synthetic data.
_T = ("government_id", "secret", "password", "drivers_license_number")
TRAIN_ONLY = {
    "x_username": [
        ("scp backup.tar.gz {user}@{hostn}:/srv/backups", ()),
        ("ssh -i key.pem {user}@{hostn} -p 2222", ()),
        ("git clone ssh://{user}@{hostn}/repo.git", ()),
        ("Log in with `ssh {user}@{hostn}` and run the deploy script.", ()),
        ("rsync -av ./site {user}@{hostn}:/var/www", ()),
        ("sftp {user}@{hostn}", ()),
        ("Failed login for user {user} from {ip}", ()),
        ("sudo -u {user} crontab -l", ()),
    ],
    "x_secret": [
        ("seed words: {seed}", ()),
        ("recovery phrase (do not share): {seed}", ()),
        ("wallet seed -> {seed}", ()),
        ("Write these {int} words down: {seed}", ()),
        ("bip39 mnemonic: {seed}", ()),
        ("my passphrase is {secret}", ()),
        ("seed phrase for the cold wallet:\n{seed}", ()),
    ],
    "x_uuid_isbn": [
        ("session_id={uuid}", _T), ("trace id {uuid} logged by the gateway", _T),
        ("Order lookup by uuid: {uuid}", _T), ("primary key: {uuid}", _T),
        ("See {isbn} for the paperback edition.", ("government_id", "passport_number")),
        ("Catalog number {hash} / {isbn}", _T), ("correlation-id: {uuid}", _T),
    ],
    "x_org_neg": [
        ("run {tool} --help to see the options", ("organization",)),
        ("Our {tool} setup is documented in the wiki.", ("organization", "person")),
        ("The {generic} uses {tool} for deployments.", ("organization",)),
        ("import {tool}", ("organization",)),
        ("Ask the {generic} lead to approve the change.", ("organization", "person")),
        ("Check the {generic} channel before you merge.", ("organization",)),
    ],
    "x_date_neg": [
        ("Standup moved to {bdate}.", ("sensitive_date", "date_of_birth")),
        ("Deadline: {bdate} EOD.", ("sensitive_date", "date_of_birth")),
        ("Q4 freeze starts {bdate}.", ("sensitive_date", "date_of_birth")),
        ("Sprint ends {bdate}; demo on {bdate2}.", ("sensitive_date", "date_of_birth")),
        ("Release candidate cut on {bdate}.", ("sensitive_date", "date_of_birth")),
        ("The conference runs {bdate} to {bdate2}.", ("sensitive_date", "date_of_birth")),
        ("Invoice dated {bdate}, net 30.", ("sensitive_date", "date_of_birth")),
        ("A short date like {sdt} appears in the log.", ("sensitive_date", "date_of_birth")),
        ("cron: next run {sdt}", ("sensitive_date", "date_of_birth")),
    ],
}

_N = ("secret", "government_id", "password")
TRAIN_ONLY.update({
    "y_secret": [   # v3: realistic seed phrases + grouped tokens, many different cues, several languages
        ("my seed phrase is {seed}", ()), ("Seed Phrase\n{seed}", ()),
        ("Here is my seed phrase, keep it safe: {seed}", ()), ("seed phrase (12 words) -> {seed}", ()),
        ("the {int} recovery words are {seed}", ()), ("Mnemonic: {seed}", ()), ("backup phrase = {seed}", ()),
        ("restore the wallet with these words: {seed}", ()),
        ("the wifi key is {gtok}", ()), ("activation key: {gtok}", ()), ("License key {gtok} (do not share)", ()),
        ("access code is {gtok}", ()), ("invite code {gtok}", ()), ("Guest wifi key -> {gtok}", ()),
        ("my vault passphrase: {secret}", ()), ("Codeword for the safe: {secret}", ()),
        ("the answer to my security question is {secret}", ()),
        ("عبارة الاسترداد هي {seed}", ()), ("La frase de recuperación es {seed}", ()),
        ("La phrase de récupération : {seed}", ()), ("Meine Wiederherstellungsphrase lautet {seed}", ()),
        ("مفتاح الواي فاي هو {gtok}", ()), ("La clave del wifi es {gtok}", ()),
    ],
    "y_username": [  # v3: the ssh/scp/db family in many shapes (the model ignored them after v2)
        ("ssh {user}@{hostn} 'uptime'", ()), ("Run: ssh {user}@{hostn}", ()),
        ("then ssh {user}@{hostn} and check the logs", ()), ("mosh {user}@{hostn}", ()),
        ("ssh -p 2222 {user}@{hostn}", ()), ("ssh -o StrictHostKeyChecking=no {user}@{hostn}", ()),
        ("scp file.txt {user}@{hostn}:~/", ()), ("scp -r ./dir {user}@{hostn}:/opt/app", ()),
        ("git remote add origin {user}@{hostn}:team/repo.git", ()), ("ansible_user={user} ansible_host={hostn}", ()),
        ("rsync -avz ./ {user}@{hostn}:/backup", ()), ("ssh {user}@{ip}", ()),
        ("ssh -i ~/.ssh/id_rsa {user}@{ip}", ()), ("psql -h {hostn} -U {user} mydb", ()),
        ("mysql -u {user} -h {hostn}", ()), ("login as {user} on {hostn}", ()), ("whoami -> {user}", ()),
        ("Username: {user}\nHost: {hostn}", ()), ("connect with user {user} to {hostn}", ()),
        ("sudo su - {user}", ()),
    ],
    "y_password": [  # v3: other cues, symbols inside the value, other languages
        ("pass: {pwd}", ()), ("pw={pwd}", ()), ("creds -> {user}:{pwd}", ()), ("password for {user} is {pwd}", ()),
        ("La contraseña es {pwd}", ()), ("Das Passwort ist {pwd}", ()), ("كلمة المرور: {pwd}", ()),
        ("Mot de passe : {pwdl}", ()), ("Password (temp): {pwdl}", ()), ("db_pass='{pwd}'", ()),
        ("PASSWORD={pwd}", ()), ("new password -> {pwdl} please confirm", ()),
        ("echo '{pwd}' | sudo -S systemctl restart app", ()), ("login {user} / pass {pwdl}", ()),
    ],
    "y_neg": [       # v3: look-alikes of grouped tokens (product refs, hashes, ids) that must NOT fire
        ("SKU {sku} is back in stock", _N), ("part number {sku}", _N), ("order ref {sku}", _N),
        ("ticket {sku} was escalated", _N), ("build {sku} passed all checks", _N),
        ("the checksum is {hash}", _N), ("trace id: {uuid}", _N), ("customer reference {sku}, invoice {inv}", _N),
    ],
})

WEIGHTS = {"person": 14, "dob": 6, "sdate": 6, "address": 8, "location": 8, "username": 6, "password": 7,
           "secret": 5, "govid": 5, "passport": 4, "dl": 4, "multi": 14, "lang": 10,
           "neg_person": 6, "neg_tech": 14, "neg_code_ui": 8, "neg_lookalike": 6, "neg_placeholder": 3, "neg_plain": 3,
           "x_username": 5, "x_secret": 5, "x_uuid_isbn": 5, "x_org_neg": 5, "x_date_neg": 6,
           "y_secret": 14, "y_username": 14, "y_password": 10, "y_neg": 6}

PREFIX = ["", "", "", "Hi, ", "Quick question: ", "FYI\n", "Hey team,\n", "Context: ", "> "]
SUFFIX = ["", "", "", " Thanks!", " Please advise.", " ASAP.", "\n\nBest regards", " Can you help?"]

TAG_RE = re.compile(r"\{([a-z_0-9]+)(?:=([^{}]*))?\}")


def split_templates(dev: bool):
    """dev gets every 5th template (index%5==4) so held-out phrasing is spread across the whole list."""
    out = {}
    for cat, lst in {**POS, **NEG}.items():
        idx = [i for i in range(len(lst)) if i % 5 == 4] or [len(lst) - 1]
        out[cat] = [lst[i] for i in idx] if dev else [t for i, t in enumerate(lst) if i not in idx]
    if not dev:
        out.update(TRAIN_ONLY)
    return out


def render(tmpl, rng, P, with_wrap=True):
    parts = []  # (text, label|None)
    if with_wrap:
        pre = rng.choice(PREFIX)
        if pre:
            parts.append((pre, None))
    pos = 0
    for m in TAG_RE.finditer(tmpl):
        if m.start() > pos:
            parts.append((tmpl[pos:m.start()], None))
        tag, lit = m.group(1), m.group(2)
        if lit is not None:                       # {label=Literal}
            parts.append((lit, tag))
        else:
            fn, label = TAGS[tag]
            val = fn(rng, P)
            if isinstance(val, list):
                parts.extend(val)
            else:
                parts.append((val, label))
        pos = m.end()
    if pos < len(tmpl):
        parts.append((tmpl[pos:], None))
    if with_wrap:
        suf = rng.choice(SUFFIX)
        if suf:
            parts.append((suf, None))
    text, spans = "", []
    for val, label in parts:
        if label:
            spans.append({"start": len(text), "end": len(text) + len(val), "label": label, "value": val})
        text += val
    # light case augmentation (ASCII only so offsets stay valid)
    if text.isascii() and rng.random() < 0.06:
        text = text.lower()
        for s in spans:
            s["value"] = s["value"].lower()
    for s in spans:
        assert text[s["start"]:s["end"]] == s["value"], (text, s)
    return text, spans


EVAL_LABEL = {"person": "PERSON", "date_of_birth": "DATE_OF_BIRTH", "sensitive_date": "SENSITIVE_DATE",
              "address": "ADDRESS", "city": "LOCATION", "state_or_region": "LOCATION",
              "postal_code": "LOCATION", "country": "LOCATION", "username": "USERNAME",
              "password": "PASSWORD", "secret": "SECRET", "government_id": "GOVERNMENT_ID",
              "passport_number": "PASSPORT_NUMBER", "drivers_license_number": "DRIVERS_LICENSE_NUMBER",
              "organization": "ORGANIZATION", "phone_number": "PHONE_NUMBER"}

# trained as GLiNER labels, but NOT scored in the test file (Tier 1 also covers cued phones)
IGNORE_IN_TEST = {"phone_number"}


SKIPPED = {"leak": 0}


_TOK = re.compile(r"\w+(?:[-_]\w+)*|\S")
MAX_SPAN_WORDS = 8     # GLiNER2 config: span_head.max_width = 8  -> longer labels cannot be learned as ONE span
CHUNK_WORDS = 6


def chunk_mention(val):
    """A label longer than the model's span limit (e.g. a 12-24 word seed phrase) is split into several adjacent
    mentions of <= CHUNK_WORDS words each, so the model learns to cover it with consecutive spans."""
    toks = [(m.start(), m.end()) for m in _TOK.finditer(val)]
    if len(toks) <= MAX_SPAN_WORDS:
        return [val]
    n = -(-len(toks) // CHUNK_WORDS)               # number of chunks
    size = -(-len(toks) // n)                      # balanced chunk size (<= CHUNK_WORDS)
    return [val[toks[i][0]:toks[min(i + size, len(toks)) - 1][1]] for i in range(0, len(toks), size)]


def build(n, seed, dev, exclude_texts, exclude_values=frozenset()):
    rng = random.Random(seed)
    P = make_pools(dev)
    T = split_templates(dev)
    cats = list(T)
    w = [WEIGHTS[c_] for c_ in cats]
    seen, rows = set(), []
    attempts = 0
    while len(rows) < n and attempts < n * 30:
        attempts += 1
        cat = rng.choices(cats, w)[0]
        tmpl, neg = rng.choice(T[cat])
        text, spans = render(tmpl, rng, P)
        if text in seen or text in exclude_texts:
            continue
        if any(v in text for v in exclude_values):
            SKIPPED["leak"] += 1
            continue
        seen.add(text)
        ents = {}
        for s in spans:
            if s["label"].startswith("~"):
                continue
            ents.setdefault(s["label"], [])
            for piece in chunk_mention(s["value"]):
                if piece not in ents[s["label"]]:
                    ents[s["label"]].append(piece)
        for lbl in neg:
            ents.setdefault(lbl, [])
        rows.append({"text": text, "entities": ents, "spans": spans, "cat": cat})
    return rows


def load_values(paths, min_len=8):
    """Every gold-span string (len>=min_len) found in the given eval files."""
    out = set()
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            for sp in d.get("spans", []):
                v = d["text"][sp["start"]:sp["end"]]
                # distinctive strings only (multi-word names/orgs or long values). Plain city names such as
                # "Cairo" are a closed vocabulary shared by every dataset - not leakage.
                if (" " in v and len(v) >= min_len) or len(v) >= 14:
                    out.add(v)
    return out


def load_texts(paths):
    out = set()
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(json.loads(line).get("text", ""))
    return out


def _stats(train, dev):
    print(f"leakage guard: skipped {SKIPPED['leak']} generated texts that contained a hold-out value")
    from collections import Counter
    for name, rows in (("train", train), ("dev", dev)):
        if not rows:
            continue
        cnt = Counter(s["label"] for r in rows for s in r["spans"] if not s["label"].startswith("~"))
        negs = sum(1 for r in rows if not r["entities"] or not any(r["entities"].values()))
        print(f"{name}: {len(rows)} examples, {sum(cnt.values())} spans, {negs} with no entities")
        print("  ", dict(sorted(cnt.items(), key=lambda x: -x[1])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-train", type=int, default=3000)
    ap.add_argument("--n-dev", type=int, default=1200)
    ap.add_argument("--only-train", action="store_true",
                    help="write train.jsonl only (keep your existing test file untouched; pass it via --exclude)")
    ap.add_argument("--write-dev-jsonl", action="store_true",
                    help="also write dev.jsonl (GLiNER2 training format). Only needed as validation data DURING fine-tuning.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="out")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="REAL eval files (hold-out): skip generated texts equal to theirs OR containing their distinctive values")
    ap.add_argument("--exclude-texts", nargs="*", default=[],
                    help="synthetic test files: skip exact duplicate texts only (their values are a closed vocabulary)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    excl = load_texts(a.exclude) | load_texts(a.exclude_texts)
    excl_vals = load_values(a.exclude)

    train = build(a.n_train, a.seed, False, excl, excl_vals)
    dev = [] if a.only_train else build(a.n_dev, a.seed + 1, True, excl | {r["text"] for r in train}, excl_vals)

    with open(out / "train.jsonl", "w", encoding="utf-8") as f:
        for r in train:
            f.write(json.dumps({"input": r["text"], "output": {"entities": r["entities"]}}, ensure_ascii=False) + "\n")
    if a.only_train:
        return _stats(train, [])
    if a.write_dev_jsonl:
        with open(out / "dev.jsonl", "w", encoding="utf-8") as f:
            for r in dev:
                f.write(json.dumps({"input": r["text"], "output": {"entities": r["entities"]}}, ensure_ascii=False) + "\n")

    # Test set in the repo's own eval format (dlp_core/eval/metrics.py):
    #   {"id", "text", "tags", "spans":[{start,end,label,tier}]}   tier=2 -> owned by Tier 2
    # tags carry "raw:<gliner label>" so the report can break LOCATION down into city/state/zip/country.
    with open(out / "semantic_test_v2.jsonl", "w", encoding="utf-8") as f:
        for i, r in enumerate(dev):
            tags = sorted({f"raw:{s['label']}" for s in r["spans"] if not s["label"].startswith("~") and s["label"] not in IGNORE_IN_TEST} | {f"cat:{r['cat']}"})
            f.write(json.dumps({"id": f"sem-{i:04d}", "text": r["text"], "tags": tags, "spans": [
                {"start": s["start"], "end": s["end"], "label": EVAL_LABEL.get(s["label"], s["label"].lstrip("~")),
                 "tier": 3 if (s["label"].startswith("~") or s["label"] in IGNORE_IN_TEST) else 2}
                for s in r["spans"]]}, ensure_ascii=False) + "\n")

    _stats(train, dev)


if __name__ == "__main__":
    main()
