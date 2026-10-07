"""Candidate patterns for Tier 1. Pure data: compiled regexes, no logic.

Rules every pattern follows:
  * LOOSE on shape, STRICT on boundaries: lookarounds (not just \\b) stop a match from
    starting or ending inside a longer number, token or URL.
  * Ambiguous shapes (3-4 digit codes, MM/YY, 9-digit numbers, bare phones) are anchored to a
    cue keyword or to an already-validated card; they are never matched bare.
  * Every repeat is bounded, so scan time stays linear.
Whether a candidate is REAL is decided in validators.py, not here.
"""
from __future__ import annotations

import re

I = re.IGNORECASE
_SEP = r"[ \t]*[\"']?[ \t]*(?:=>|:=|[:=\-#]|\bis\b)?[ \t]*[\"']?[ \t]*"

# ------------------------------------------------------------------ payment cards
# Groups separated by 1-2 spaces/hyphens (PDF extraction often doubles spaces).
CARD_RUN = re.compile(r"(?<!\d)(?:\d+(?:[ \-]{1,2}|[ \t]*\r?\n[ \t]*)){0,7}\d+(?!\d)")
CARD_CTX = re.compile(r"\b(?:card|credit|debit|visa|master\s?card|amex|american\s+express|discover|cc|pan|payment)\b", I)

CVV_KEYWORD = re.compile(
    r"(?<![A-Za-z])(?:cvv2?|cvc2?|csc|cid|cvn|security\s+code|card\s+verification(?:\s+(?:value|code|number))?)(?![A-Za-z])"
    r"(?:[_ \t]*(?:code|number|no\.?|#))?" + _SEP + r"(?P<v>(?<!\d)\d{3,4})(?!\d|[./-]\d)",
    I,
)
EXPIRY_KEYWORD = re.compile(
    r"(?<![A-Za-z])(?:exp(?:iry|iration|ires)?(?:[_ \t]+date)?\.?|valid[ \t]+(?:thru|through|until|till)|expires?[ \t]+on)(?![A-Za-z])"
    + _SEP + r"(?P<v>(?<![\d/])(?:0[1-9]|1[0-2])[ \t]?[/\-.|][ \t]?(?:\d{4}|\d{2})(?!\d))",
    I,
)
CARD_ADJ_EXP = re.compile(
    r"[\s,;:|/-]{1,4}(?:(?:exp(?:iry|iration|ires)?\.?|valid[ \t]+(?:thru|through|until))[ \t]*[:=]?[ \t]*)?"
    r"(?P<v>(?:0[1-9]|1[0-2])[ \t]?[/\-.|][ \t]?(?:\d{4}|\d{2}))(?!\d)",
    I,
)
CARD_ADJ_CVV = re.compile(
    r"[\s,;:|/-]{1,4}(?:(?P<kw>cvv2?|cvc2?|csc|cid)[ \t]*[:=]?[ \t]*)?(?P<v>\d{3,4})(?!\d|[./-]\d)", I
)

# ------------------------------------------------------------------ bank identifiers
IBAN_START = re.compile(r"(?<![A-Za-z0-9])(?P<cc>[A-Za-z]{2})(?P<chk>\d{2})(?=[ ]?[A-Za-z0-9])")
IBAN_CTX = re.compile(r"\biban\b", I)
SWIFT = re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?(?![A-Za-z0-9])")
SWIFT_CTX = re.compile(r"\b(?:swift|bic|wire|beneficiary|correspondent|intermediary|bank\s+code)\b", I)
ABA_NINE = re.compile(r"(?<![\d-])\d{9}(?![\d-])")
ABA_CTX_BEFORE = re.compile(
    r"(?<![A-Za-z])(?:aba|routing|rtn|transit|fedwire|ach)(?:[_ \t]*(?:number|no\.?|num|#|code))?" + _SEP + r"$", I
)
ABA_CTX_AFTER = re.compile(r"^[ \t]*[(\[]?[ \t]*(?:routing|aba)", I)
BANK_ACCOUNT = re.compile(
    r"(?<![A-Za-z0-9])(?:bank[_ \t]+)?(?:account|acct|a/c)(?:[_ \t]*(?:number|num|no\.?|#))?(?![A-Za-z])"
    + _SEP + r"(?P<v>\d(?:[ -]?\d){5,16})(?![\d-])",
    I,
)
CRYPTO_BTC = re.compile(r"(?<![A-Za-z0-9])[13][A-HJ-NP-Za-km-z1-9]{25,34}(?![A-Za-z0-9])")
CRYPTO_BECH32 = re.compile(r"(?<![A-Za-z0-9])(?:bc1|tb1|BC1|TB1)[A-Za-z0-9]{11,87}(?![A-Za-z0-9])")
CRYPTO_ETH = re.compile(r"(?<![A-Za-z0-9])0x[0-9a-fA-F]{40}(?![A-Za-z0-9])")

# ------------------------------------------------------------------ government / tax / health
SSN_DASHED = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
SSN_BARE = re.compile(r"(?<![\d-])\d{3}[ ]?\d{2}[ ]?\d{4}(?!\d)")
SSN_CTX_BEFORE = re.compile(
    r"(?<![A-Za-z])(?:ssn|social[_ \t]+security(?:[_ \t]+(?:number|no\.?|#))?|soc\.?[ \t]*sec\.?)" + _SEP + r"$", I
)
TAX_ID = re.compile(
    r"(?<![A-Za-z0-9])(?:EIN|TIN|tax[_ \t]+id(?:entification)?(?:[_ \t]+(?:number|no\.?))?|federal[_ \t]+tax[_ \t]+id"
    r"|VAT(?:[_ \t]+(?:reg(?:istration)?[_ \t]+)?(?:number|no\.?|id))?)(?![A-Za-z])"
    + _SEP + r"(?P<v>\d{2}-\d{7}|\d{9}|[A-Z]{2}[ ]?[A-Z0-9]{8,12})(?![A-Za-z0-9-])",
    I,
)
_ID_VALUE = r"(?P<v>(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{%d,19})(?![A-Za-z0-9-])"
MRN = re.compile(
    r"(?<![A-Za-z0-9])(?:MRN|medical[_ \t]+record(?:[_ \t]+(?:number|no\.?|num|#))?|patient[_ \t]+(?:id|number|no\.?|#))"
    r"(?![A-Za-z])" + _SEP + _ID_VALUE % 4,
    I,
)
HEALTH_INS = re.compile(
    r"(?<![A-Za-z0-9])(?:member[_ \t]+id|subscriber[_ \t]+id|insurance[_ \t]+(?:id|number|no\.?)|policy[_ \t]+(?:number|no\.?|id)"
    r"|health[_ \t]+plan[_ \t]+(?:id|number)|beneficiary[_ \t]+(?:id|number)|medicare[_ \t]+(?:number|id|no\.?)|HICN|MBI)"
    r"(?![A-Za-z])" + _SEP + _ID_VALUE % 5,
    I,
)

# ------------------------------------------------------------------ contact
# A preceding ':' is allowed (so `mailto:a@b.com` and `email:a@b.com` match); the password half
# of a URL's `user:password@host` is rejected separately via EMAIL_REJECT_BEFORE.
EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.){1,8}[A-Za-z]{2,24}(?![A-Za-z0-9-])"
)
EMAIL_REJECT_BEFORE = re.compile(r"://(?:[^\s/@]{0,128}:)?$")   # URL userinfo: scheme://user[:pass]@host
# Phone: the VALUE pattern is loose; it only counts when a cue word precedes it.
PHONE_VALUE = re.compile(r"(?<![\w])\+?\(?\d[\d ().\-]{5,18}\d(?![\w])")
PHONE_CUE_BEFORE = re.compile(
    r"(?<![A-Za-z])(?:phone|tel|telephone|mobile|cell|call(?:[_ \t]+me)?(?:[_ \t]+(?:at|on))?|fax|whatsapp|contact)"
    r"(?:[_ \t]*(?:number|no\.?|num|#))?(?:[ \t]+is)?[ \t:=\-#.\"'>]*$", I
)
PHONE_E164 = re.compile(r"(?<![\w+])\+[1-9](?:[ .\-]?\(?\d{1,4}\)?){2,6}(?!\w)")
PHONE_NANP = re.compile(r"(?<![\w.+-])(?:\+?1[ .-]?)?(?:\(\d{3}\)[ ]?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w-])")

# ------------------------------------------------------------------ network and infrastructure
IPV4 = re.compile(
    r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?!\w|\.\d)"
)
IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
URL_ANY = re.compile(
    r"\bhttps?://(?:[^\s/@\"'<>]{1,128}@)?(?:\[[0-9a-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)"
    r"(?::\d{1,5})?(?:[/?#][^\s\"'<>`]{0,2000})?",
    I,
)


def build_internal_host_pattern(suffixes) -> re.Pattern:
    """Hostnames ending in an internal suffix (.internal, .corp, tenant domains, ...)."""
    alt = "|".join(re.escape(s) for s in sorted(set(suffixes), key=len, reverse=True))
    label = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    return re.compile(
        rf"(?<![A-Za-z0-9.@_-])(?:{label}\.){{1,6}}(?:{alt})(?![A-Za-z0-9_-]|\.[A-Za-z0-9])", I
    )


# ------------------------------------------------------------------ secrets and credentials
PEM_FULL = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----[\s\S]{0,16384}?-----END (?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----"
)
# Pasted without the END line: header + base64 lines.
PEM_TRUNCATED = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?-----[ \t]*\r?\n(?:[A-Za-z0-9+/=]{16,200}\r?\n?){1,300}"
)
JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{6,2048}\.ey[A-Za-z0-9_-]{4,4096}\.[A-Za-z0-9_-]{0,1024}(?![A-Za-z0-9_-])")
BEARER = re.compile(r"(?<![A-Za-z0-9])Bearer[ \t]+(?P<v>[A-Za-z0-9._~+/=-]{16,2048})(?![A-Za-z0-9._~+/=-])", I)
BASIC_AUTH = re.compile(r"Authorization[ \t]*:[ \t]*Basic[ \t]+(?P<v>[A-Za-z0-9+/=]{8,2048})", I)
GOOGLE_OAUTH = re.compile(r"(?<![A-Za-z0-9_-])ya29\.[A-Za-z0-9_-]{20,2048}")

# (name, regex, minimum entropy): vendor-prefixed signatures. Prefix + length + entropy
# is strong evidence even without a keyword.
API_KEY_SIGNATURES = (
    ("aws_access_key_id", r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|AROA|APKA|ASCA)[A-Z0-9]{16}(?![A-Z0-9])", 0.0),
    ("github_token", r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})(?![A-Za-z0-9_])", 3.0),
    ("gitlab_pat", r"(?<![A-Za-z0-9_-])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", 3.0),
    ("slack_token", r"(?<![A-Za-z0-9-])xox[abposr]-[A-Za-z0-9-]{10,72}(?![A-Za-z0-9-])", 2.5),
    ("slack_webhook", r"https://hooks\.slack\.com/services/T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{16,}", 0.0),
    ("stripe_secret", r"(?<![A-Za-z0-9_])(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,99}(?![A-Za-z0-9_])", 3.0),
    ("google_api_key", r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])", 3.0),
    ("llm_provider_key", r"(?<![A-Za-z0-9_-])sk-(?:ant-|proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,200}(?![A-Za-z0-9_-])", 3.3),
    ("sendgrid", r"(?<![A-Za-z0-9_.-])SG\.[A-Za-z0-9_-]{16,32}\.[A-Za-z0-9_-]{16,64}(?![A-Za-z0-9_-])", 3.0),
    ("twilio_api_key", r"(?<![A-Za-z0-9])SK[0-9a-fA-F]{32}(?![A-Za-z0-9])", 3.0),
    ("npm_token", r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{36}(?![A-Za-z0-9_])", 3.0),
    ("huggingface", r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}(?![A-Za-z0-9_])", 3.0),
    ("pypi_token", r"(?<![A-Za-z0-9_-])pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}", 3.0),
)
GENERIC_SECRET_KV = re.compile(
    r"(?<![A-Za-z0-9])(?P<k>[\w-]{0,20}?(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret"
    r"|secret[_-]?(?:access[_-]?)?key|private[_-]?token|app[_-]?secret|secret|token)[\w-]{0,10})"
    r"[ \t]*[\"']?[ \t]*(?:=>|:=|=|:)[ \t]*[\"']?(?P<v>[A-Za-z0-9_\-+/=.~]{16,256})(?![A-Za-z0-9_\-+/=.~])",
    I,
)
AWS_SECRET_KV = re.compile(
    r"(?:aws_secret_access_key|aws_secret|secret_access_key|secretaccesskey)[ \t]*[\"']?[ \t]*(?:=>|:=|=|:)[ \t]*[\"']?"
    r"(?P<v>[A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])",
    I,
)
AZURE_SECRET_KV = re.compile(
    r"(?:AccountKey|SharedAccessKey|SharedAccessSignature|Client[_ ]?Secret)[ \t]*=[ \t]*(?P<v>[A-Za-z0-9+/=%._~-]{20,512})", I
)
AWS_KEY_ID = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
B64_40 = re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])")
PASSWORD = re.compile(
    r"(?<![A-Za-z0-9])[\w-]{0,30}?(?:password|passwd|pwd|passphrase)"
    r"(?![_-]?(?:policy|policies|length|min|max|rules?|hint|label|field|required|strength|reset|expir\w*|enabled|regex|pattern|prompt|type|placeholder|confirm\w*)(?![a-z]))"
    r"[\w-]{0,15}[\"']?[ \t]*(?:=>|:=|=|:|\bis\b)[ \t]*(?:is[ \t]+)?"
    r"[\"']?(?P<v>(?<=[\"'])[^\"'\r\n]{3,128}(?=[\"'])|[^\s\"',;&`]{4,128})",
    I,
)
CONN_URI = re.compile(
    r"(?<![A-Za-z0-9+.-])(?:jdbc:[a-z0-9]+|postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql|sqlserver"
    r"|oracle|cassandra|couchdb|clickhouse|memcached|ftps?|sftps?|smtps?|ldaps?|https?)://[^\s\"'<>`]{3,512}",
    I,
)
CONN_KV = re.compile(
    r"(?:server|data[ \t]+source|host)[ \t]*=[ \t]*[^;\r\n]{1,200};(?:[^;\r\n]{1,200};){0,8}?"
    r"(?:password|pwd)[ \t]*=[ \t]*[^;\r\n]{1,128}",
    I,
)
RECOVERY_HEAD = re.compile(r"(?<![A-Za-z])(?:recovery|backup)[ \t]+(?:codes?|keys?)(?![A-Za-z])[ \t]*[:\-]?", I)
RECOVERY_TOKEN = re.compile(
    r"[ \t\r\n,;]*(?P<c>(?=[A-Za-z0-9-]*\d)(?:[A-Za-z0-9]{4,8}(?:-[A-Za-z0-9]{3,8}){1,3}|\d{8,10}))(?![A-Za-z0-9-])"
)
