"""Validation logic only: no regexes that find candidates, no I/O.

Every function takes the candidate string and returns bool. A validator answers
"is this really a <thing>?", which is what keeps harmless numbers from being redacted.
Hard checksums (Luhn, mod-97, ABA, base58check, bech32) are kept separate from
plausibility checks (phone length, entropy) so callers can treat them differently.
"""
from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

_NON_DIGIT = re.compile(r"\D")


def digits_only(s: str) -> str:
    return _NON_DIGIT.sub("", s)


def _ascii_digits(s: str) -> bool:
    return bool(s) and s.isascii() and s.isdigit()


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    counts: dict = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(c / n * math.log2(c / n) for c in counts.values())


# ------------------------------------------------------------------ payment cards
def luhn_valid(digits: str) -> bool:
    if not _ascii_digits(digits):
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = ord(ch) - 48
        if i & 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def card_brand(digits: str):
    """Issuer brand from IIN prefix + length, or None. Cuts Luhn false positives ~10x."""
    n = len(digits)
    if not _ascii_digits(digits) or n < 13:
        return None
    p2, p3, p4, p6 = int(digits[:2]), int(digits[:3]), int(digits[:4]), int(digits[:6])
    if digits[0] == "4" and n in (13, 16, 19):
        return "visa"
    if n == 16 and (51 <= p2 <= 55 or 2221 <= p4 <= 2720):
        return "mastercard"
    if n == 15 and p2 in (34, 37):
        return "amex"
    if 16 <= n <= 19 and (p4 == 6011 or p2 == 65 or 644 <= p3 <= 649 or 622126 <= p6 <= 622925):
        return "discover"
    if 14 <= n <= 19 and (300 <= p3 <= 305 or p3 == 309 or p2 in (36, 38, 39)):
        return "diners"
    if 16 <= n <= 19 and 3528 <= p4 <= 3589:
        return "jcb"
    if 16 <= n <= 19 and p2 == 62:
        return "unionpay"
    return None


def expiry_valid(value: str) -> bool:
    """MM/YY or MM/YYYY with a real month; 4-digit years must be 2000-2099."""
    parts = re.split(r"[ \t]*[/\-.|][ \t]*", value.strip())
    if len(parts) != 2 or not all(_ascii_digits(p) for p in parts):
        return False
    month, year = parts
    if not 1 <= int(month) <= 12:
        return False
    return len(year) == 2 or (len(year) == 4 and 2000 <= int(year) <= 2099)


# ------------------------------------------------------------------ bank identifiers
IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22,
    "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28,
    "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23,
    "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26,
    "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18,
    "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24,
    "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24,
    "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}  # verify against the current SWIFT IBAN registry before release


def iban_valid(raw: str) -> bool:
    s = re.sub(r"[ \-]", "", raw).upper()
    expected = IBAN_LENGTHS.get(s[:2])
    if expected is None or len(s) != expected or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", s):
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def aba_valid(digits: str) -> bool:
    """US routing number: Federal Reserve prefix + weighted 3-7-1 mod-10 checksum."""
    if len(digits) != 9 or not _ascii_digits(digits):
        return False
    prefix = int(digits[:2])
    if not (prefix <= 12 or 21 <= prefix <= 32 or 61 <= prefix <= 72 or prefix == 80):
        return False
    return sum(int(c) * w for c, w in zip(digits, (3, 7, 1) * 3)) % 10 == 0


ISO_COUNTRIES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS "
    "BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE "
    "EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM "
    "HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC "
    "LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA "
    "NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW "
    "SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO "
    "TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW".split()
)


def swift_valid(code: str) -> bool:
    if len(code) not in (8, 11) or not re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?", code):
        return False
    return code[4:6] in ISO_COUNTRIES


# ------------------------------------------------------------------ government / tax ids
def ssn_valid(value: str) -> bool:
    m = re.fullmatch(r"(\d{3})[- ]?(\d{2})[- ]?(\d{4})", value.strip())
    if not m:
        return False
    area, group, serial = m.groups()
    return area not in ("000", "666") and not area.startswith("9") and group != "00" and serial != "0000"


_EIN_PREFIXES = frozenset(
    list(range(1, 7)) + list(range(10, 17)) + list(range(20, 28)) + list(range(30, 40))
    + list(range(40, 49)) + list(range(50, 69)) + list(range(71, 78)) + list(range(80, 89))
    + list(range(90, 96)) + [98, 99]
)
_EU_VAT = frozenset("AT BE BG CY CZ DE DK EE EL ES FI FR GB HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK XI".split())


def tax_id_valid(value: str) -> bool:
    v = value.strip()
    if re.fullmatch(r"\d{2}-?\d{7}", v):
        return int(v[:2]) in _EIN_PREFIXES
    return bool(re.fullmatch(r"[A-Z]{2}[ ]?[A-Z0-9]{8,12}", v)) and v[:2] in _EU_VAT


# ------------------------------------------------------------------ phone / network
def e164_plausible(value: str) -> bool:
    d = digits_only(value)
    return 8 <= len(d) <= 15 and d[0] != "0" and len(set(d)) > 1


def nanp_valid(value: str) -> bool:
    d = digits_only(value)
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    if len(d) != 10 or len(set(d)) == 1:
        return False
    area, exch = d[:3], d[3:6]
    return area[0] in "23456789" and area[1:] != "11" and exch[0] in "23456789" and exch[1:] != "11"


def ipv4_valid(value: str, ignore_loopback: bool = True) -> bool:
    try:
        ip = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    if ignore_loopback and (ip.is_loopback or ip.is_unspecified or ip.is_multicast or value == "255.255.255.255"):
        return False
    return True


def ipv6_valid(value: str, ignore_loopback: bool = True) -> bool:
    try:
        ip = ipaddress.IPv6Address(value)
    except ValueError:
        return False
    if sum(c in "0123456789abcdefABCDEF" for c in value) < 4:  # drops "a::b" style code tokens
        return False
    return not (ignore_loopback and (ip.is_loopback or ip.is_unspecified))


def ip_is_private(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_private
    except ValueError:
        return False


def is_internal_host(host: str, suffixes) -> bool:
    h = host.lower().strip("[]").rstrip(".")
    if not h or h == "localhost":
        return False
    try:
        ip = ipaddress.ip_address(h)
        return (ip.is_private or ip.is_link_local) and not ip.is_loopback and not ip.is_unspecified
    except ValueError:
        pass
    if "." not in h:
        return True  # single-label intranet name such as http://jenkins/
    return any(h == s or h.endswith("." + s) for s in suffixes)


def internal_url_valid(value: str, suffixes) -> bool:
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return False
    return bool(host) and is_internal_host(host, suffixes)


def bare_internal_hostname_valid(host: str, suffixes) -> bool:
    """Bare hostnames need >=2 labels before the suffix, or a digit/hyphen in the first label.
    Keeps code like `self.local` from being treated as infrastructure."""
    h = host.lower()
    for s in sorted(suffixes, key=len, reverse=True):
        if h.endswith("." + s):
            labels = h[: -(len(s) + 1)].split(".")
            return len(labels) >= 2 or any(c.isdigit() or c == "-" for c in labels[0])
    return False


# ------------------------------------------------------------------ crypto addresses
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def base58check_valid(addr: str) -> bool:
    n = 0
    for ch in addr:
        i = _B58.find(ch)
        if i < 0:
            return False
        n = n * 58 + i
    try:
        raw = n.to_bytes(25, "big")
    except OverflowError:
        return False
    checksum = hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4]
    return checksum == raw[-4:] and raw[0] in (0x00, 0x05, 0x6F, 0xC4)


def _bech32_polymod(values) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    return chk


def bech32_valid(addr: str) -> bool:
    if addr.lower() != addr and addr.upper() != addr:
        return False
    a = addr.lower()
    pos = a.rfind("1")
    if pos < 1 or pos + 7 > len(a) or len(a) > 90:
        return False
    hrp, data = a[:pos], [_B32.find(c) for c in a[pos + 1:]]
    if -1 in data:
        return False
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data
    return _bech32_polymod(values) in (1, 0x2BC830A3)  # bech32, bech32m


# ------------------------------------------------------------------ tokens, keys, secrets
def _b64url_json(part: str):
    try:
        raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        return json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def jwt_valid(token: str) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False
    header, payload = _b64url_json(parts[0]), _b64url_json(parts[1])
    return isinstance(header, dict) and "alg" in header and isinstance(payload, (dict, list))


def basic_auth_valid(b64: str) -> bool:
    try:
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    return b":" in raw and all(32 <= c < 127 for c in raw)


def pem_valid(block: str) -> bool:
    lines = [ln.strip() for ln in block.splitlines()]
    body = "".join(ln for ln in lines
                   if ln and not ln.startswith("-----") and ":" not in ln and not ln.startswith("="))
    if len(body) < 40:
        return False
    try:
        base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


_PLACEHOLDER = re.compile(
    r"(?i)^(?:x{4,}|\*{4,}|\.{3,}|<[^>]*>|\$\{.*\}|\{\{.*\}\}|%[sd]|\$\w+|your[_-]?\w*|"
    r"placeholder|redacted|0{6,}|\[.*\])$"
)
_REFERENCE = re.compile(
    r"(?i)^(?:process\.env|os\.environ|os\.getenv|getenv|env\.|config\.|settings\.|args\.|params\.|"
    r"self\.|this\.|secrets\.|vault\.|ENV\[)"
)
_NOT_A_SECRET_WORDS = frozenset(
    "none null nil true false string str any bool boolean number int integer required optional "
    "undefined empty hidden masked stored hashed reset expired incorrect invalid wrong missing "
    "correct text input field value password passwd pwd secret token key".split()
)


def looks_like_reference(value: str) -> bool:
    """Variable names, env lookups and function calls are not secret values."""
    return bool(_REFERENCE.match(value)) or value.endswith(")") or "(" in value


def secret_value_ok(value: str, min_entropy: float = 3.0, min_len: int = 16) -> bool:
    return (len(value) >= min_len and not _PLACEHOLDER.match(value)
            and not looks_like_reference(value) and shannon_entropy(value) >= min_entropy)


def password_value_ok(value: str) -> bool:
    v = value.strip()
    return (len(v) >= 3 and v.lower() not in _NOT_A_SECRET_WORDS and not _PLACEHOLDER.match(v)
            and not looks_like_reference(v) and not re.fullmatch(r"[A-Z_]{4,}", v))


def connection_string_valid(value: str) -> bool:
    if value.lower().startswith("jdbc:"):
        return True
    try:
        parts = urlsplit(value)
        has_password = bool(parts.password)
    except ValueError:
        return False
    db_schemes = {"postgres", "postgresql", "mysql", "mariadb", "mongodb", "mongodb+srv", "redis",
                  "rediss", "amqp", "amqps", "mssql", "sqlserver", "oracle", "cassandra", "couchdb",
                  "clickhouse", "memcached"}
    return has_password or parts.scheme.lower() in db_schemes


# ------------------------------------------------------------------ misc text validators
_FILE_EXT_TLDS = frozenset("png jpg jpeg gif svg webp bmp ico css js mjs json map woff woff2 ttf".split())


def email_valid(value: str) -> bool:
    local, _, domain = value.rpartition("@")
    if not local or len(local) > 64 or ".." in value or local[0] == "." or local[-1] == ".":
        return False
    labels = domain.split(".")
    if len(domain) > 253 or len(labels) < 2 or any(not 1 <= len(x) <= 63 for x in labels):
        return False
    tld = labels[-1].lower()
    return tld.isalpha() and len(tld) >= 2 and tld not in _FILE_EXT_TLDS


def mrn_valid(value: str) -> bool:
    """Medical Record Number — no universal checksum, so we apply a strict
    plausibility filter: 6-20 chars, mixed alphanumerics, at least 3 digits,
    no common false-positive shapes (dates, phone numbers, ZIP codes).
    """
    v = value.strip().upper().replace(" ", "").replace("-", "")
    n = len(v)
    if not 6 <= n <= 20:
        return False
    digit_count = sum(c.isdigit() for c in v)
    if digit_count < 3:
        return False
    # Reject pure dates (MMDDYYYY or YYYYMMDD)
    if digit_count == 8 and v.isdigit():
        mm = int(v[:2]) if n >= 2 else 0
        if 1 <= mm <= 12:
            return False
    # Reject ZIP-code-shaped (5 digits only)
    if digit_count == 5 and v.isdigit() and n == 5:
        return False
    # Reject phone-number-shaped (10 digits only)
    if digit_count == 10 and v.isdigit() and n == 10:
        return False
    # Must have at least one letter OR be 7+ digits (facility IDs are long)
    has_letter = any(c.isalpha() for c in v)
    if not has_letter and digit_count < 6:
        return False
    return True


def bank_account_valid(value: str) -> bool:
    """Bank account number — no universal checksum, so we apply plausibility:
    6-17 digits, not all same, not a Luhn-valid card (handled separately),
    and not a date or phone number shape.
    """
    d = digits_only(value)
    n = len(d)
    if not 6 <= n <= 17 or len(set(d)) == 1:
        return False
    # Card numbers are handled by the card recognizer
    if n >= 13 and luhn_valid(d):
        return False
    # Reject 8-digit dates (MMDDYYYY)
    if n == 8:
        mm = int(d[:2])
        if 1 <= mm <= 12:
            return False
    # Reject 10-digit phone shapes (area code + number)
    if n == 10:
        area = int(d[:3])
        if area >= 200:
            return False
    return True


def year_in_range(year: int) -> bool:  # helper for tests/policy
    return 1900 <= year <= datetime.date.today().year + 1
