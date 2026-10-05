# Local Backend — Data Plane

FastAPI service on `localhost:8765`. Orchestrates the tiered-hybrid pipeline
(detection → routing → decision → masking → residual scan → demasking)
and hosts the Mapping Vault, the system's most sensitive component.

## Layout

```
app/
├── api/               # /detect, /mask, /demask, /process_file endpoints
├── pipeline/          # the 5 pipeline steps
│   ├── detection.py    # orchestrator — calls Tier 1 engine + Tier 2 semantic
│   ├── routing.py      # entity → action (redact / faker / keep)
│   ├── decision.py     # SLM tier (System-1 LLM, future work)
│   ├── masking.py      # Faker substitution + vault storage + apply_pairs
│   ├── residual_scanner.py  # independent last-gate scan (fail-closed)
│   ├── chunker.py      # sentence-boundary chunking for long inputs
│   └── tier1/          # deterministic detection engine (9 files)
├── multimodal/         # file parsers + reconstructors (PDF, Word, Excel, text)
├── vault/              # Mapping Vault: bijective, collision-checked, 24h expiry
└── security/           # origin check + per-install token
```

## Quickstart

```bash
cd local-backend

# Install dependencies
pip install -r requirements.txt

# Start the backend
uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

Verify it's running:
```bash
curl http://127.0.0.1:8765/health
# → {"status":"ok","service":"pii-gateway-backend","version":"1.0.0"}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mask` | POST | Detect + mask PII in text. Returns `masked_text`, `entity_types`, `entities_found`, `leaks`, `safe_to_send`. |
| `/api/demask` | POST | Restore real values in LLM response using vault. |
| `/api/detect` | POST | Detect PII only (no masking). Returns entities. |
| `/api/process_file` | POST | Process a file upload (extract → mask → reconstruct). |
| `/health` | GET | Health check (no auth). |
| `/token` | GET | Get per-install token (for extension setup). |

### Mask endpoint example

```bash
curl -X POST http://127.0.0.1:8765/api/mask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "My card is 4242424242424242, email sarah@example.com", "conversation_id": "chat_123"}'
```

Response:
```json
{
  "masked_text": "My card is [[REDACTED]], email robertsjennifer@example.com",
  "entities_found": 2,
  "entity_types": ["CREDIT_CARD", "EMAIL"],
  "leaks": [],
  "safe_to_send": true
}
```

> **Privacy:** The response does NOT include `pairs` (which contain real values).
> The extension applies masks per-slot using `masked_text` directly.

## Detection Engine

The detection engine is the core of the system — it identifies what PII exists
in a prompt or file before masking happens. It uses a **tiered hybrid architecture**:

```
Input text
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Tier 1 — Deterministic (regex + checksums)                │
│  ComplianceValidatorEngine (app/pipeline/tier1/)           │
│  24 entity types · ~0ms latency · ~100% precision          │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Tier 2 — Semantic (GLiNER2-PII via ONNX)                  │
│  Person names, organizations, locations                     │
│  TODO: model not yet integrated (M4 milestone)             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Merge & Conflict Resolution                                │
│  Overlap resolution: prefer longer match, drop contained   │
│  Duplicate collapse: same span+label → keep best score      │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
  list[Entity]
```

### Tier 1 — Deterministic (`app/pipeline/tier1/`)

The deterministic tier catches **structured PII** — anything with a recognizable
format or checksum. It runs first, always, on every input.

**Architecture:**

```
tier1/
├── engine.py          # ComplianceValidatorEngine — entry point
├── patterns.py        # 50 boundary-safe regex patterns (data only)
├── validators.py      # Mathematical validators (Luhn, mod-97, ABA, etc.)
├── recognizers.py      # Glue: pattern → validator → Span
├── registry.py         # Single wiring point (add entities here)
├── normalizer.py       # NFKC + zero-width removal + offset map
├── config.py           # EngineConfig (internal suffixes, etc.)
├── types.py            # Span dataclass
└── __init__.py
```

**Design rules:**

1. **Loose patterns, strict validators.** The regex finds *candidates*; the
   validator confirms whether a candidate is real. This keeps harmless numbers
   from being masked (e.g., a 16-digit order ID won't match unless it passes Luhn).

2. **Boundary-safe lookarounds.** Every pattern uses `(?<!\d)` and `(?!\d)` —
   not just `\b` — to prevent matching inside longer numbers. This stops the
   overlapping-span bug where a CVV matches inside a card number.

3. **Keyword-anchored ambiguity.** Entities like CVV (3-4 digits) and expiry
   dates (MM/YY) are too short to match standalone — they require a keyword
   like `cvv:` or `exp:` nearby, OR adjacency to a validated card number.

4. **Obfuscation defense.** The normalizer strips zero-width characters,
   converts fullwidth digits to ASCII, and folds en-dashes to hyphens —
   then maps all spans back to original offsets so masking edits the real text.

**24 entity types detected:**

| Category | Entity types | Validator |
|----------|-------------|-----------|
| **Payment (PCI-DSS)** | `CREDIT_CARD`, `CVV`, `CARD_EXPIRY` | Luhn + issuer brand, keyword-anchored |
| **Banking** | `IBAN`, `SWIFT_BIC`, `ABA_ROUTING`, `BANK_ACCOUNT_NUMBER` | mod-97, ISO country, 3-7-1 mod-10, plausibility |
| **Crypto** | `CRYPTO_WALLET` | base58check (Bitcoin), bech32 (segwit), 0x prefix (ETH) |
| **Government / Health (HIPAA)** | `US_SSN`, `TAX_ID`, `MEDICAL_RECORD_NUMBER`, `HEALTH_INSURANCE_ID` | SSN area/group rules, EIN prefix, plausibility |
| **Contact** | `EMAIL`, `PHONE_NUMBER` | RFC 5322 + TLD, E.164 / NANP plausibility |
| **Network (SOC2)** | `IP_ADDRESS`, `INTERNAL_URL`, `INTERNAL_HOSTNAME` | ipaddress module, internal suffix list |
| **Secrets (SOC2 CC6)** | `API_KEY`, `AUTH_TOKEN`, `PRIVATE_KEY`, `CLOUD_SECRET`, `CONNECTION_STRING`, `PASSWORD`, `RECOVERY_CODE` | Prefix + entropy, JWT structure, PEM base64, Shannon entropy |

**Validators (mathematical):**

| Validator | What it checks | Used for |
|-----------|---------------|----------|
| Luhn | mod-10 checksum on digits | Credit cards |
| mod-97 | IBAN international checksum | IBAN |
| ABA 3-7-1 | US routing number checksum | ABA routing |
| base58check | Bitcoin address checksum | BTC wallets |
| bech32 | Bitcoin segwit checksum | Bech32 wallets |
| JWT structure | 3 base64url segments, `alg` in header | JWT tokens |
| PEM structure | base64 body between BEGIN/END | Private keys |
| Shannon entropy | bits/char ≥ threshold | Secrets, API keys |
| Placeholder rejection | `xxxx`, `****`, `your_key` | Prevents masking config |
| Reference rejection | `process.env.X`, `os.getenv` | Prevents masking code |

**Cleanup passes (overlap resolution):**

1. **Exact-duplicate collapse** — same start/end/label keeps the best score
2. **Numeric containment** — a CVV/expiry/phone inside a validated card/IBAN/key is dropped
3. **CVV vs date mutual exclusion** — a span can't be both; keep the stronger one

### Tier 2 — Semantic (GLiNER2-PII)

**Status:** Not yet integrated (M4 milestone). The integration point exists in
`detection.py` (`_detect_semantic` + `_run_gliner_inference`) but the ONNX
model is not loaded.

**Planned:** GLiNER2-PII via ONNX runtime, label-conditioned inference
(`PERSON`, `ORGANIZATION`, `LOCATION`), with sentence-boundary chunking for
inputs longer than 512 tokens (handled by `chunker.py`).

### Merge & Conflict Resolution

The merge step is currently in `detection.py` (simple: deduplicate, sort by
start offset, prefer longer match). A dedicated `merge_engine.py` with
per-label confidence thresholds and fuzz testing is planned (Phase 3 of the
detection action plan).

## Masking Pipeline

After detection, entities flow through:

```
entities → routing.py (redact/faker/keep) → masking.py → vault → masked output
```

**Routing rules:**

| Action | When | Examples |
|--------|------|----------|
| `redact` | Secrets and financial IDs (can't be faked — checksums would break) | Cards, API keys, IBANs, SSNs, private keys |
| `faker` | Identity PII (realistic surrogate is safer than redaction) | Names, emails, phones, organizations |
| `keep` | Non-PII infrastructure | Public IPs, public URLs |

**Vault:** Per-conversation, bijective, 24h TTL. Same real value → same fake
within a conversation. Cross-conversation, same real value → different fakes
(no fingerprinting).

## Residual Scanner

The last gate before send. Runs deterministic checks on the **masked output**
to catch anything the detection pipeline missed. It is **independent** of the
primary detector — does not share code. If it finds any structured PII
(cards, keys, JWTs, IBANs), the request is **fail-closed** (blocked).

## Multimodal Pipeline

Extracts text from files, runs detection + masking, and reconstructs the file
in place — preserving layout, fonts, images, and formatting.

| Format | Parse | Reconstruct |
|--------|-------|-------------|
| Text (`.txt`, `.md`, `.csv`) | ✅ | ✅ |
| PDF (`.pdf`) | ✅ | ✅ (redaction + insert at position) |
| Word (`.docx`) | ✅ (body + tables + headers/footers) | ✅ |
| Excel (`.xlsx`) | ✅ | ✅ |

Unprocessable files (images, scans, encrypted) are **rejected** — never
silently passed through.

## Tests

```bash
cd local-backend
pip install -r requirements.txt
pytest tests/ -v
```

```
149 passed, 1 skipped (3.6s)
```

| Test file | What it covers |
|-----------|---------------|
| `test_deterministic.py` | Tier 1 engine: cards, API keys, JWT, IBAN, email, phone, IP, SSN, crypto, overlap resolution |
| `test_masking.py` | Faker substitution, vault reuse, redaction, routing coverage |
| `test_residual_scanner.py` | Independent last-gate: cards, JWTs, API keys, IBANs, entropy |
| `test_vault_bijective.py` | Bijective mapping, collision detection, expiry, persistence |
| `test_chunker.py` | Sentence-boundary chunking, overlap, offset remapping |
| `test_multimodal_pipeline.py` | PDF, Word, Excel, text — extract + mask + reconstruct |
| `test_pdf_reconstructor.py` | In-place PDF redaction preserving layout |
| `test_file_type_detector.py` | File type detection by extension + magic bytes |
| `test_security.py` | Token verification, origin check |
| `test_api.py` | API endpoint integration tests |

## Configuration

```python
from app.pipeline.tier1 import EngineConfig

config = EngineConfig(
    internal_suffixes=("internal", "intranet", "corp", "lan", "local"),
    tenant_domains=("corp.acme.com",),  # tenant-specific internal domains
    ignore_loopback_ips=True,             # 127.0.0.1, ::1 are harmless
    private_ip_label="IP_ADDRESS",        # or "INTERNAL_HOSTNAME" to route RFC1918 as infra
    max_text_len=2_000_000,               # chunk larger inputs
)
```
