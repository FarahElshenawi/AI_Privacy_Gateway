# Local Backend — Data Plane

FastAPI service on `localhost:8765`. Runs the `dlp_core` detection engine
(Tier 1 + Tier 2), offset-based masking, encrypted vault, and an independent
residual scanner that fail-closes if any PII survives.

## Layout

```
dlp_core/                              ← DETECTION ENGINE (pure Python, 142 tests)
├── span.py                            # Span contract (start, end, label, score, source, validated)
├── policy.py                          # Label → Action (REDACT / FAKER / KEEP; unknown = REDACT)
├── merge.py                           # MergeEngine (unions overlaps, strictest action wins)
├── masker.py                          # OffsetMasker (offset-based, no str.replace) + Demasker
├── vault.py                           # InMemoryVault (Fernet-encrypted, HMAC-keyed, thread-safe)
├── detection.py                      # DetectionPipeline (parallel tiers, timeouts, degraded reporting)
├── residual_scanner.py               # Independent last-gate (checksums only, fail-closed)
├── tier1/                            # Deterministic (regex + validators)
│   ├── engine.py                      # Tier1Engine.scan(text) → list[Span]
│   ├── patterns.py                    # 50+ boundary-safe regex patterns
│   ├── validators.py                  # Luhn, mod-97, ABA, base58check, bech32, JWT, PEM
│   ├── recognizers.py                 # Card/IBAN/AWS/Recovery/DenyTerm recognizers
│   ├── registry.py                    # Single wiring point (add entity = one line)
│   └── config.py                     # Tier1Config (tenant domains, deny-terms, phone switch)
├── tier2/                            # Semantic (GLiNER2-PII)
│   ├── engine.py                      # Tier2Engine — PyTorch + ONNX dual mode
│   ├── config.py                     # Tier2Config (model, labels, threshold, chunking)
│   └── export_onnx.py               # One-time ONNX export script
└── eval/                             # Evaluation harness
    ├── metrics.py                    # Char-level leak recall, strict/overlap F1, CI
    ├── run.py                         # Runner (one command reproduces the report)
    ├── make_holdout.py                # Hold-out generator (298 cases, 325 gold spans)
    ├── holdout_v1.jsonl               # Frozen hold-out (tamper-checked)
    └── report.json                    # Tier 1 baseline (0.974 leak recall)

app/                                   ← FASTAPI LAYER (thin wrappers around dlp_core)
├── api/                              # HTTP endpoints
│   ├── mask.py                        # POST /api/mask → DetectionPipeline → OffsetMasker → scan
│   ├── demask.py                     # POST /api/demask → Demasker
│   ├── detect.py                     # POST /api/detect → DetectionPipeline (no masking)
│   └── process_file.py               # POST /api/process_file → multimodal → Pipeline
├── pipeline/
│   ├── engine.py                     # Shared singleton: wires dlp_core for FastAPI
│   ├── chunker.py                    # Sentence-boundary chunking (for Tier 2 long text)
│   └── decision.py                  # SLM adjudication stub (fail-closed: redacts)
├── multimodal/                       # File parsers + reconstructors
│   ├── pipeline.py                   # extract → detect → mask → reconstruct
│   ├── parsers/                      # PDF, Word, Excel, text
│   └── reconstructors/               # offset-based per format
└── security/                         # Per-install token, origin check
```

## Quickstart

```bash
cd local-backend

# Install dependencies (includes gliner2, torch, transformers for Tier 2)
pip install -r requirements.txt

# Start the backend
uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

Verify:
```bash
curl http://127.0.0.1:8765/health
# → {"status":"ok","service":"pii-gateway-backend","version":"1.0.0"}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mask` | POST | Detect + mask PII. Returns `masked_text`, `entity_types`, `safe_to_send`, `degraded`. |
| `/api/demask` | POST | Restore real values in LLM response using vault. |
| `/api/detect` | POST | Detect PII only (no masking). Returns spans with offsets. |
| `/api/process_file` | POST | Process a file upload (extract → mask → reconstruct). |
| `/health` | GET | Health check (no auth). |
| `/token` | GET | Get per-install token. Loopback only. |

### Security model

- Binds to `127.0.0.1`; the `Host` header must be a loopback name (blocks DNS rebinding).
- CORS and the Origin check allow only the Doppel extension's pinned ID (`efcejekkbkbbknfpjgbpkojgnoomggbi`, set by the `key` in `extension/manifest.json`); other extensions, web pages and dev servers are rejected. Extra IDs: `DLP_EXTENSION_IDS`.
- Every `/api/*` call, including `/api/policies/*` (which change what gets masked), needs `Authorization: Bearer <per-install token>`. The token lives in
  `~/.pii_gateway_token.json` (mode `0600`); an empty/missing token never authenticates.

### Mask endpoint

```bash
curl -X POST http://127.0.0.1:8765/api/mask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "My name is Farah Ahmed, card 4242424242424242", "conversation_id": "chat_123"}'
```

Response:
```json
{
  "masked_text": "My name is [FAKE NAME], card [REDACTED:CREDIT_CARD]",
  "entities_found": 2,
  "entity_types": ["CREDIT_CARD", "PERSON"],
  "leaks": [],
  "safe_to_send": true,
  "degraded": false,
  "blocked": false
}
```

- `degraded: true` → Tier 2 (GLiNER) failed/timed out; names not detected. Send with warning.
- `blocked: true` → Tier 1 (critical) failed. Do NOT send.

## Detection Engine

### Architecture

```
                           text
                             │
                 ┌───────────┼───────────┐
                 ▼           ▼           ▼
            ┌─────────┐ ┌─────────┐
            │ Tier 1  │ │ Tier 2  │
            │ regex   │ │ GLiNER2 │
            │ 24 types│ │ 17 types│
            │~1ms/KB  │ │~50-200ms│
            └────┬────┘ └────┬────┘
                 │           │
                 └─────┬─────┘
                       ▼
              ┌─────────────────┐
              │  MergeEngine    │  unions overlaps, strictest action wins
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │  OffsetMasker   │  offset-based, never str.replace
              │  + Vault        │  Fernet-encrypted fake↔real
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ ResidualScanner │  independent, checksums only, fail-closed
              └────────┬────────┘
                       │
                       ▼
              masked_text + safe_to_send + degraded
```

### Tier 1 — Deterministic (`dlp_core/tier1/`)

Catches **structured PII** with regex patterns + mathematical validators.
Runs first, always, on every input. ~1ms per KB.

**24 entity types:**

| Category | Types | Validator |
|----------|-------|-----------|
| Payment (PCI-DSS) | `CREDIT_CARD`, `CVV`, `CARD_EXPIRY` | Luhn + issuer brand |
| Banking | `IBAN`, `SWIFT_BIC`, `ABA_ROUTING`, `BANK_ACCOUNT_NUMBER` | mod-97, 3-7-1 mod-10 |
| Crypto | `CRYPTO_WALLET` | base58check, bech32 |
| Government/Health (HIPAA) | `US_SSN`, `TAX_ID`, `MEDICAL_RECORD_NUMBER`, `HEALTH_INSURANCE_ID` | SSN rules, EIN prefix |
| Contact | `EMAIL`, `PHONE_NUMBER` | RFC 5322, E.164/NANP (cue-anchored) |
| Network (SOC2) | `IP_ADDRESS`, `INTERNAL_URL`, `INTERNAL_HOSTNAME` | ipaddress, tenant config |
| Secrets (SOC2 CC6) | `API_KEY`, `AUTH_TOKEN`, `PRIVATE_KEY`, `CLOUD_SECRET`, `CONNECTION_STRING`, `PASSWORD`, `RECOVERY_CODE` | Prefix + entropy, JWT, PEM, Shannon |

**Eval baseline (frozen hold-out, 298 cases):**
- Character-level leak recall: **0.974**
- Strict F1: **0.975**
- Benign false positives: **1/40**
- P95 latency: **0.45ms**

### Tier 2 — Semantic (`dlp_core/tier2/`)

Uses **GLiNER2-PII** (`fastino/gliner2-privacy-filter-PII-multi`, 205M params)
for context-dependent PII that Tier 1 can't catch.

**17 semantic labels:**

| GLiNER label | Our label | Routing |
|---|---|---|
| person, full_name, first_name, last_name | `PERSON` | FAKER |
| date_of_birth | `DATE_OF_BIRTH` | FAKER |
| address, street_address | `ADDRESS` | FAKER |
| city, state_or_region, postal_code, country | `LOCATION` | FAKER |
| username | `USERNAME` | FAKER |
| password, secret | `PASSWORD` / `SECRET` | REDACT |
| government_id, passport_number, drivers_license_number | `GOVERNMENT_ID` etc. | REDACT |
| sensitive_date | `SENSITIVE_DATE` | FAKER |

**Two inference modes:**

| Mode | How | Speed | Setup |
|------|-----|-------|-------|
| **PyTorch** (default) | `GLiNER2.from_pretrained()` + `extract_entities_long()` | ~50-200ms | `pip install gliner2 torch transformers` |
| **ONNX** (optional) | `onnxruntime.InferenceSession()` | 2-4x faster | Run export first (see below) |

**ONNX export (one-time):**
```bash
python -m dlp_core.tier2.export_onnx
# Creates: models/gliner2_pii.onnx
```

Then set the env variable or hardcode the path in `app/pipeline/engine.py`:
```bash
export GLINER_ONNX_PATH=models/gliner2_pii.onnx
```

**Threshold:** 0.3 (low = high recall). DLP prioritizes recall — missed PII = data leak.

**Chunking:** GLiNER2's `extract_entities_long()` handles texts > 384 tokens
with built-in chunking (chunk_size=384, overlap=64).

**Failure handling:** Non-critical. If GLiNER fails to load or times out,
`DetectionResult.degraded = True` and the pipeline continues with Tier 1 only.
The API response includes `degraded: true` so the extension can warn the user.

### Merge Engine (`dlp_core/merge.py`)

All tiers emit `Span` objects. The MergeEngine:

1. **Unions overlaps** — never drops a span (fixes the partial-overlap leak)
2. **Strictest action wins** — `REDACT > FAKER > KEEP`
3. **Evidence ranks** — `VALIDATED > CONTEXT > MODEL`, then score, then length
4. **Word-boundary snapping** — widens partial detections to whole tokens
5. **Fuzz-tested** — 300 random inputs prove the coverage invariant

### Offset Masker (`dlp_core/masker.py`)

Masks by **character offset**, never by `str.replace`:

1. Sort spans, replace **right-to-left** in one pass
2. Replacement text is never re-scanned (no cascading replacement)
3. "John" never corrupts "Johnson" — only flagged offsets are touched
4. FAKER spans that can't find a collision-free fake degrade to REDACT
5. `MaskResult` contains no real values — they go only to the sealed vault

### Vault (`dlp_core/vault.py`)

- **Fernet-encrypted** — real values stored as AES-128-CBC + HMAC tokens
- **HMAC-keyed reverse index** — no plaintext, no brute-forceable hash
- **Bijective** — both directions enforced (real→fake and fake→real)
- **Per-conversation** — same person gets same fake within a chat; different fakes across chats
- **24h TTL** — entries expire and are lazily purged
- **Thread-safe** — RLock on all operations

### Residual Scanner (`dlp_core/residual_scanner.py`)

Independent last gate — shares **no patterns** with Tier 1. Scans masked
output for surviving PII with hard checksums only:

- Luhn-valid credit cards
- mod-97 valid IBANs
- JWT structure (3 segments + `alg` field)
- API key prefixes (AKIA, sk-, ghp_, AIza, xox)
- PEM blocks (private keys)

Does NOT scan for emails/phones — Faker surrogates also match those patterns.
If any finding is returned, `safe_to_send = False` (fail-closed).

## DetectionPipeline (`dlp_core/detection.py`)

Runs all tiers in **parallel** with per-tier timeouts:

```python
pipe = DetectionPipeline([
    DetectorSpec(Tier1Engine(), critical=True, timeout_s=0.5),
    DetectorSpec(Tier2Engine(), critical=False, timeout_s=5.0),
])
result = pipe.run(text)
# result.merged          → disjoint spans to mask
# result.blocked         → True if critical detector failed (do NOT send)
# result.degraded        → True if any detector didn't complete (send with warning)
# result.uncovered_labels → labels nobody checked (e.g. PERSON while Tier 2 is down)
```

**Fail-closed rules:**
- Tier 1 failure → `blocked = True` (request blocked)
- Tier 2 failure → `degraded = True` (request sent with warning)
- Timeout → detector abandoned, reported as `TIMEOUT`
- Exception → only class name in report (no user text in error messages)
- Stuck detector → remembered, prevents thread pile-up

## Multimodal Pipeline

Extracts text from files, runs detection + masking, reconstructs in place.

| Format | Parse | Reconstruct |
|--------|-------|-------------|
| Text (`.txt`, `.md`, `.csv`) | ✅ | ✅ |
| PDF (`.pdf`) | ✅ | ✅ (redaction + insert at position) |
| Word (`.docx`) | ✅ (body + tables + headers/footers) | ✅ |
| Excel (`.xlsx`) | ✅ | ✅ |

Unprocessable files (images, scans, encrypted) are **rejected** — never
silently passed through.

- **File type comes from the bytes, not the name.** A zip is Word if it holds `word/document.xml`,
  Excel if it holds `xl/workbook.xml`; any other zip, legacy `.doc`/`.xls`, or a `.pdf`/`.docx`/`.xlsx`
  name without the matching bytes is rejected as unknown.
- **Tables keep their column header as scan context.** Excel columns, Word table columns and CSV/TSV
  columns are scanned as `Header: value`, so a bare cell under "SSN" or "Phone" is recognised. The header
  text is read-only context and is never edited or written back.
- **Numeric Excel cells are scanned** (a card number stored as a number is masked; the cell becomes text).
- **CSV/TSV** are masked cell by cell with delimiters, quotes and line endings preserved exactly.

## Tests

```bash
cd local-backend
pip install -r requirements.txt
pytest dlp_core/ tests/ -v
```

```
142 passed in 3.2s
```

| Test file | Tests | What it covers |
|-----------|-------|----------------|
| `dlp_core/test_core.py` | 35 | Merge invariants, offset masking, vault bijection, fuzz |
| `dlp_core/tier1/test_tier1.py` | 48 | All 24 entity types, boundary safety, obfuscation, adversarial input |
| `dlp_core/test_detection.py` | 11 | Pipeline: timeout, stuck detector, concurrency, fail-closed |
| `dlp_core/eval/test_eval.py` | 4 | Harness: metrics on hand-made cases |
| `tests/test_api.py` | 10 | API endpoint integration |
| `tests/test_chunker.py` | 18 | Sentence-boundary chunking |
| `tests/test_security.py` | 15 | Token verification, origin check |
| `tests/test_pdf_reconstructor.py` | 1 | PDF in-place redaction |

## Configuration

### Tier 1

```python
from dlp_core.tier1 import Tier1Config

config = Tier1Config(
    internal_suffixes=("internal", "intranet", "corp", "lan", "local"),
    tenant_domains=("corp.acme.com",),   # tenant-specific internal domains
    deny_terms=("Project Falcon",),      # words that must never leave
    ignore_loopback_ips=True,
    emit_uncued_phones=False,             # Tier 2 handles uncued phones
)
```

### Tier 2

```python
from dlp_core.tier2 import Tier2Config

config = Tier2Config(
    model_name="fastino/gliner2-privacy-filter-PII-multi",
    threshold=0.3,                        # low = high recall
    use_onnx=False,                       # True after running export_onnx.py
    onnx_path=None,                       # "models/gliner2_pii.onnx"
    chunk_size=384,                       # GLiNER2 token window
    chunk_overlap=64,
    enabled=True,
)
```

### Policy (label → action)

```python
from dlp_core import Policy, Action

policy = Policy(default=Action.REDACT)  # unknown labels = REDACT (fail-closed)
# Override per tenant:
policy = policy.with_overrides(IP_ADDRESS=Action.REDACT)  # redact all IPs
```


## Persistence and cloud sync (optional)

| Env var | Default | Purpose |
|---------|---------|---------|
| `DLP_VAULT_PERSIST` | `true` | Keep fake↔real mappings across restarts (encrypted SQLite). `false` = memory only. |
| `DLP_VAULT_DB_PATH` | `~/.pii_gateway_vault.db` | Vault database (mode 0600). Expiry uses wall-clock time (24 h TTL). |
| `DLP_VAULT_KEY` | key file `~/.pii_gateway_vault.key` | Fernet key. The default key file sits in the same home folder as the database, so it protects against copying the DB alone, not against someone with access to your account. Use `DLP_VAULT_KEY` from a secret store for stronger protection. |
| `DLP_TIER1_PER_KCHAR_S` / `DLP_TIER2_PER_KCHAR_S` | `0.05` / `0.5` | Extra time budget per 1000 characters on top of `DLP_TIERn_TIMEOUT_S`, so big inputs are not judged like chat prompts. Caps: `DLP_TIERn_MAX_S` (prompts, 10 / 20) and `DLP_TIERn_FILE_MAX_S` (file batches, 60 / 300). Measure your hardware with `python benchmark_tier2.py`. |
| `DLP_MIN_SCORES_FILE` | unset | JSON of per-label minimum scores from the bake-off (`{"PERSON": 0.35}` or a report with `min_scores`). Non-validated spans below their label's floor are dropped; validated spans and critical secrets are never affected. A bad file stops startup. |
| `DLP_FILE_STRICT` | `true` | `/process_file` returns 422 instead of a file when any detector tier was degraded (e.g. Tier 2 down: names would go out unmasked). Set `false` to allow degraded files with the `X-DLP-Degraded` header. |
| `CLOUD_URL` / `CLOUD_ENROLL_KEY` | unset | Enable cloud sync: enroll once with the enrollment key, then use the per-device token (saved 0600 in `~/.pii_gateway_cloud_endpoint.json`, override with `DLP_CLOUD_TOKEN_FILE`) for heartbeat, audit metadata and policy pull. The org admin key is never used here. `CLOUD_URL` must be `https://` (or localhost). |

Pulled policies change live masking, except that critical secrets (cards, keys, SSN, …) can never be set to `keep`.
Audit events that can't be delivered are retried (bounded buffer), and demask calls are audited as `demask` events.


## Tenant config (cloud-managed)

With cloud sync enabled, the org's **deny terms** (project/client names that must never leave) and
**internal domains** (`corp.acme.com`: every `*.corp.acme.com` host is internal) are pulled from the cloud
with the policies and applied live to Tier 1. Edit them with `PUT /api/tenant-config` (admin key).
