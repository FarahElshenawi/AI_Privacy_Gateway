# AI Privacy Gateway (Doppel)

> **Tagline:** *Looks like your data. Acts like your data. Never is your data.*

A local-first AI privacy gateway that intercepts prompts and file uploads in the browser, masks PII before it reaches the LLM, and stores the mapping locally — **no PII ever leaves your machine.**

## How It Works

```
User types prompt / uploads file in ChatGPT
         │
         ▼
  Chrome Extension (chrome.debugger + CDP Fetch domain)
    Intercepts at the NETWORK layer — below page, page SW, and page CSP
         │
         ▼
  Local Backend (FastAPI, 127.0.0.1:8765)
    ├── 1. DETECT (Tier 1 regex+checksums ∥ Tier 2 GLiNER2-PII)
    ├── 2. MERGE (union overlaps, strictest action wins)
    ├── 3. MASK (offset-based, never str.replace)
    ├── 4. RESIDUAL SCAN (independent, fail-closed)
    └── 5. RECONSTRUCT (for files: per-segment offset edits)
         │
         ▼
  Masked text/file sent to ChatGPT (LLM only sees surrogates)
         │
         ▼
  ChatGPT responds (with fake values)
         │
         ▼
  NOTE: Response demasking is NOT yet wired into the extension.
  Today the user sees surrogate values (e.g. [REDACTED:CREDIT_CARD]) in responses.
```

**PII never crosses the network.** Only masked surrogates are sent to OpenAI.

---

## Architecture

### Two-Process Local Design

```
┌─────────────────────────────────────────────────────────────────────┐
│  BROWSER                                                             │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Service Worker (only actor — no content scripts)             │    │
│  │  ├── chrome.debugger.attach({tabId}, "1.3")                  │    │
│  │  ├── Fetch.enable (CDP network-layer interception)           │    │
│  │  ├── Network.enable (body retrieval via networkId)            │    │
│  │  ├── On Fetch.requestPaused → mask → Fetch.continueRequest   │    │
│  │  └── holds the install token                                  │    │
│  └──────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                     │ HTTP (127.0.0.1:8765)
┌────────────────────────────────────┼─────────────────────────────────┐
│  LOCAL BACKEND (trusted)            ▼                                 │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  dlp_core — Detection Engine (pure Python, 220 tests)          │   │
│  │                                                                │   │
│  │  ┌─────────────────────────────────────────────────────────┐  │   │
│  │  │  DetectionPipeline (parallel tiers, timeouts, degraded) │  │   │
│  │  │                                                         │  │   │
│  │  │  ┌──────────┐  ┌──────────┐                            │  │   │
│  │  │  │ Tier 1   │  │ Tier 2   │                            │  │   │
│  │  │  │ regex    │  │ GLiNER2  │                            │  │   │
│  │  │  │ 24 types │  │ 17 labels│                            │  │   │
│  │  │  │ ~1ms/KB  │  │ ~50-200ms│                            │  │   │
│  │  │  │ CRITICAL │  │ DEGRADED │                            │  │   │
│  │  │  └────┬─────┘  └────┬─────┘                            │  │   │
│  │  │       └──────┬───────┘                                 │  │   │
│  │  │              ▼                                          │  │   │
│  │  │       MergeEngine (union, strictest wins)              │  │   │
│  │  │              ▼                                          │  │   │
│  │  │       OffsetMasker (offset-based, no str.replace)       │  │   │
│  │  │       + Vault (Fernet-encrypted, HMAC-keyed)            │  │   │
│  │  │              ▼                                          │  │   │
│  │  │       ResidualScanner (independent, fail-closed)         │  │   │
│  │  └─────────────────────────────────────────────────────────┘  │   │
│  │                                                                │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐  │   │
│  │  │ SegmentMasker   │  │ Per-format      │  │ Eval harness │  │   │
│  │  │ (for files)     │  │ handlers        │  │ + bake-off   │  │   │
│  │  └─────────────────┘  └─────────────────┘  └──────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  app/ — FastAPI Layer (thin wrappers)                         │   │
│  │  ├── api/ (mask, demask, detect, process_file)                │   │
│  │  ├── pipeline/engine.py (shared singleton)                   │   │
│  │  ├── multimodal/handlers/ (PDF, Word, Excel, text)           │   │
│  │  └── security/ (token, origin check)                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Trust Boundaries

- **Browser extension SW:** The only actor on the browser side. Intercepts at CDP layer — below page scripts, page workers, and page CSP. No content scripts, no DOM injection.
- **Local backend:** All sensitive processing happens here. Same machine, different process. Holds the install token.
- **Cloud (future):** Cut by default. If present, carries metadata only (counts, timing) — never prompt content.

---

## Quickstart

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Chrome 116+ (for `chrome.debugger` support)

### 1. Set up the Local Backend

```bash
cd local-backend

# Install dependencies (includes gliner2, torch, transformers for Tier 2)
uv sync --extra dev

# Start the backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

Verify:
```bash
curl http://127.0.0.1:8765/health
# → {"status":"ok","service":"pii-gateway-backend","version":"1.0.0","tier1":"ready","tier2":"warming_up"}
```

On first start, Tier 2 (GLiNER2) loads in a background thread (~10-30s). Until ready, `/health` reports `"tier2":"warming_up"` and name detection is unavailable (degraded mode).

### 2. Load the Chrome Extension

1. Open `chrome://extensions` in Chrome
2. Enable **Developer Mode** (top right toggle)
3. Click **Load unpacked** → select the `extension/` folder
4. Accept the `debugger` permission warning
5. Visit `chatgpt.com` — a yellow banner appears: "Doppel is debugging this browser"

### 3. Test It

Type a prompt in ChatGPT containing PII:
> "My name is Farah Ahmed, my card is 4242424242424242, email sarah@example.com"

The extension will:
1. Intercept the request via CDP `Fetch.requestPaused`
2. Send to backend for detection + masking
3. Replace the body with masked text
4. Forward to OpenAI via `Fetch.continueRequest` (base64-encoded)

ChatGPT receives: "My name is [FAKE NAME], my card is [REDACTED:CREDIT_CARD], email [FAKE EMAIL]"

---

## Detection Engine (`dlp_core/`)

### Architecture

```
                           text
                             │
                 ┌───────────┼───────────┐
                 ▼           ▼
            ┌─────────┐ ┌─────────┐
            │ Tier 1  │ │ Tier 2  │
            │ regex   │ │ GLiNER2 │
            │ 24 types│ │ 17 types│
            │~1ms/KB  │ │~50-200ms│
            │VALIDATED │ │ MODEL   │
            └────┬────┘ └────┬────┘
                 └─────┬─────┘
                       ▼
              ┌─────────────────┐
              │  MergeEngine    │  unions overlaps, strictest action wins
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │  OffsetMasker   │  offset-based, never str.replace
              │  + Vault        │  Fernet-encrypted fake↔real
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │ ResidualScanner │  independent, checksums only, fail-closed
              └────────┬────────┘
                       ▼
              masked_text + safe_to_send + degraded
```

### Tier 1 — Deterministic (`dlp_core/tier1/`)

Catches **structured PII** with regex patterns + mathematical validators. Runs first, always, on every input. ~1ms per KB. **Critical** — failure blocks the request.

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

Uses **GLiNER2-PII** (`fastino/gliner2-privacy-filter-PII-multi`, 205M params) for context-dependent PII that Tier 1 can't catch. **Non-critical** — failure degrades (names not detected, request continues with warning).

**17 semantic labels:** person, full_name, address, city, country, username, password, organization, date_of_birth, government_id, passport_number, drivers_license_number, sensitive_date, etc.

**Bake-off results (461 cases, 6 models tested on Colab GPU):**

| Model | Best F1 | Leak Recall | PERSON F1 | Latency p50 |
|-------|---------|-------------|-----------|-------------|
| **GLiNER2-PII-multi** | **0.896** | **0.986** | **0.953** | 366ms |
| urchade/gliner_multi_pii-v1 | 0.844 | 0.880 | 0.919 | 25ms |
| knowledgator/gliner-pii-base | 0.859 | 0.865 | 0.806 | 15ms |
| nvidia/gliner-PII | 0.817 | 0.959 | 0.630 | 46ms |

GLiNER2 won on F1, leak recall, and PERSON F1. All labels met target threshold — no fine-tuning needed.

**Inference mode:** PyTorch (ONNX export failed — model architecture has dynamic control flow that `torch.onnx.export` can't trace).

**Threshold:** 0.3 (low = high recall — missed PII = data leak). For production: 0.75 (fewer false positives, 0.986 leak recall).

### DetectionPipeline (`dlp_core/detection.py`)

Runs all tiers in **parallel** with per-tier timeouts:

```python
result = pipeline.run(text)
result.merged           # disjoint spans to mask
result.blocked          # True if critical detector failed → do NOT send
result.degraded         # True if any detector didn't complete → send with warning
result.uncovered_labels # labels nobody checked (e.g. PERSON while Tier 2 is down)
```

### MergeEngine (`dlp_core/merge.py`)

- **Unions overlaps** — never drops a span (no partial-overlap leak)
- **Strictest action wins** — `REDACT > FAKER > KEEP`
- **Evidence ranks** — `VALIDATED > CONTEXT > MODEL`, then score, then length
- **Word-boundary snapping** — widens partial detections to whole tokens
- **Fuzz-tested** — 300 random inputs prove the coverage invariant

### OffsetMasker (`dlp_core/masker.py`)

Masks by **character offset**, never by `str.replace`:
- Right-to-left replacement in one pass
- "John" never corrupts "Johnson"
- FAKER spans without a collision-free fake degrade to REDACT
- Vault stores real values Fernet-encrypted with HMAC-keyed reverse index

### ResidualScanner (`dlp_core/residual_scanner.py`)

Independent last gate — shares **no patterns** with Tier 1. Only checks entities with hard checksums:
- Luhn-valid credit cards (with issuer prefix check)
- mod-97 valid IBANs
- JWT structure (3 segments + `alg` field)
- API key prefixes (AKIA, sk-, ghp_, AIza, xox)
- PEM blocks (private keys)

Does NOT scan for emails/phones — Faker surrogates also match those. If any finding is returned, `safe_to_send = False` (fail-closed).

---

## File Masking (`dlp_core/segments.py` + `app/multimodal/handlers/`)

Files are processed per-segment (page, paragraph, cell) with offset-based edits — **no `str.replace` anywhere**.

| Format | Handler | How it masks | Fails closed on |
|--------|---------|-------------|-----------------|
| Text | `handlers/text.py` | Offset edits, preserves BOM + encoding | n/a |
| Word | `handlers/word.py` | Paragraph→run mapping (headers, tables, text boxes, hyperlinks) | Tracked deletions, comments, embedded objects |
| Excel | `handlers/excel.py` | String cells, formula literals, comments, hidden sheets | Sheet names with PII |
| PDF | `handlers/pdf.py` | Character-level glyph redaction via PyMuPDF | Scans, image-only pages, encrypted files |

**Fail-closed rules:**
- Scanned/image-only PDFs → rejected (`no_extractable_text`)
- Failed critical detector → no output file
- Masked file re-read and residual-scanned
- `strict=True` blocks when coverage incomplete

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mask` | POST | Detect + mask PII. Returns `masked_text`, `entity_types`, `safe_to_send`, `degraded`, `coverage_complete`, `uncovered_labels`. |
| `/api/demask` | POST | Restore real values using vault. (Not wired into extension yet.) |
| `/api/detect` | POST | Detect PII only (no masking). Returns spans with offsets. |
| `/api/process_file` | POST | Process a file upload (extract → mask → reconstruct). 50 MB cap. |
| `/health` | GET | Health check + tier status (`tier1`, `tier2`). No auth. |
| `/token` | GET | Get per-install token. Loopback only. |

### `/mask` response

```json
{
  "masked_text": "My name is [FAKE NAME], card [REDACTED:CREDIT_CARD]",
  "entities_found": 2,
  "entity_types": ["CREDIT_CARD", "PERSON"],
  "leaks": [],
  "safe_to_send": true,
  "degraded": false,
  "blocked": false,
  "coverage_complete": true,
  "uncovered_labels": [],
  "degraded_reasons": [],
  "strict": false
}
```

- `degraded: true` → Tier 2 failed/timed out; names not detected. Send with warning.
- `blocked: true` → Tier 1 (critical) failed. Do NOT send.

---

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DLP_TIER1_TIMEOUT_S` | 2.0 | Tier 1 timeout (critical — exceeding blocks) |
| `DLP_TIER2_TIMEOUT_S` | 3.0 | Tier 2 timeout (exceeding degrades) |
| `DLP_TIER2_ENABLED` | true | Enable/disable GLiNER2 |
| `DLP_WARM_TIER2` | true | Pre-load model at startup in background thread |
| `DLP_STRICT` | false | Block when coverage incomplete (production: set true) |
| `DLP_MAX_TEXT_CHARS` | 200000 | Max text length for `/mask` and `/detect` |

---

## Tests

```bash
cd local-backend
uv run pytest dlp_core/ tests/ -v
```

```
220 tests pass (with stub models; 2 require real gliner2 install)
```

| Test file | Tests | What it covers |
|-----------|-------|----------------|
| `dlp_core/test_core.py` | 35 | Merge invariants, offset masking, vault bijection, fuzz |
| `dlp_core/tier1/test_tier1.py` | 48 | All 24 entity types, boundary safety, obfuscation, adversarial input |
| `dlp_core/test_detection.py` | 11 | Pipeline: timeout, stuck detector, concurrency, fail-closed |
| `dlp_core/test_tier2.py` | 9 | Offset verification, every-occurrence masking, warmup, availability |
| `dlp_core/test_segments.py` | 8 | SegmentMasker: batch, clip, offset edits |
| `dlp_core/test_residual.py` | 4 | Independent scanner: issuer prefix, no text in leaks |
| `dlp_core/test_bakeoff.py` | 7 | Bake-off orchestration, threshold sweep, kill test |
| `dlp_core/eval/test_eval.py` | 4 | Metrics: char-level leak recall, F1, confidence intervals |
| `tests/test_api.py` | 10 | API endpoint integration |
| `tests/test_mask_coverage.py` | 8 | Coverage reporting, strict mode, degraded handling |
| `tests/test_multimodal.py` | 12 | PDF, Word, Excel, text — offset masking per segment |
| `tests/test_process_file_api.py` | 4 | File upload endpoint |
| `tests/test_security.py` | 15 | Token verification, origin check |
| `tests/test_chunker.py` | 18 | Sentence-boundary chunking |

---

## Evaluation

### Tier 1 baseline (frozen hold-out, 298 cases)

| Metric | Value |
|--------|-------|
| Character-level leak recall | 0.974 |
| Strict F1 | 0.975 |
| Benign false positives | 1/40 |
| P95 latency | 0.45ms |

### Tier 2 bake-off (461 cases, 6 models)

GLiNER2-PII-multi selected. All labels met target threshold — no fine-tuning needed.

### Bake-off tool

```bash
python -m dlp_core.eval.bakeoff --candidates eval/candidates.json --out eval/bakeoff --device cuda
```

Each candidate runs in its own subprocess (crash isolation). Thresholds swept offline. See `eval/BAKEOFF.md` for full instructions.

---

## Project Structure

```
AI_Privacy_Gateway/
├── local-backend/               # FastAPI backend + detection engine
│   ├── dlp_core/                # Pure Python detection engine (220 tests)
│   │   ├── span.py              # Span contract (start, end, label, score, source, validated)
│   │   ├── policy.py            # Label → Action (unknown = REDACT, fail-closed)
│   │   ├── merge.py             # MergeEngine (union overlaps, strictest wins)
│   │   ├── masker.py            # OffsetMasker (offset-based, no str.replace) + Demasker
│   │   ├── vault.py             # InMemoryVault (Fernet-encrypted, HMAC-keyed, thread-safe)
│   │   ├── detection.py         # DetectionPipeline (parallel tiers, timeouts, degraded)
│   │   ├── segments.py          # SegmentMasker (per-segment offset edits for files)
│   │   ├── residual_scanner.py # Independent last-gate (checksums only, fail-closed)
│   │   ├── tier1/               # Deterministic (regex + validators) — 24 entity types
│   │   ├── tier2/               # Semantic (GLiNER2-PII) — 17 labels
│   │   └── eval/                # Eval harness + bake-off + frozen hold-outs
│   ├── app/                     # FastAPI layer (thin wrappers)
│   │   ├── api/                 # mask, demask, detect, process_file
│   │   ├── pipeline/engine.py   # Shared singleton wiring
│   │   ├── multimodal/handlers/ # Per-format file handlers (PDF, Word, Excel, text)
│   │   └── security/            # Token, origin check
│   └── tests/                   # Integration tests
├── extension/                   # Chrome extension (Manifest V3, chrome.debugger)
│   ├── manifest.json            # debugger permission, host_permissions
│   ├── src/background/          # CDP interception, masking calls
│   └── src/popup/               # Popup UI (stats, attach status)
├── cloud-backend/               # Cloud control plane (future work — not connected)
├── dashboard/                   # Admin dashboard (future work)
├── eval/                        # Evaluation sets + scoring scripts
├── docs/                        # Architecture, ADRs
└── pyproject.toml               # Root project config (uv)
```

---

## Key Design Decisions

1. **chrome.debugger over MAIN-world fetch override.** CDP `Fetch.enable` intercepts at the network layer — below page SW, page workers, and page CSP. Survives ChatGPT bundle changes. ~150 LOC vs ~600 LOC.
2. **Offset-based masking, never str.replace.** Eliminates cascading replacement, substring corruption, and "John" in "Johnson" bugs.
3. **Fail-closed.** If any step fails, the request is blocked. No raw PII ever leaves the device.
4. **Per-conversation vault.** Same real value → same fake within a chat. Cross-conversation → different fakes (no fingerprinting). Fernet-encrypted at rest.
5. **Independent residual scanner.** Last gate, shares no code with primary detector. Checksums only (Luhn, mod-97, JWT structure, API key prefixes).
6. **Honest degradation.** `degraded`, `uncovered_labels`, `coverage_complete` in every response. Client always knows what wasn't checked.
7. **Faker over anchors.** Descriptive anchors rejected after empirical eval (76% re-identifiability vs 14% chance baseline).

---

## Limitations (Stated Honestly)

- **Response demasking not wired.** User sees `[REDACTED:CREDIT_CARD]` and faker surrogates in ChatGPT responses. The vault + Demasker exist but the extension doesn't call `/api/demask` yet.
- **No OCR.** Scanned PDFs and images are rejected (fail-closed), not silently passed through.
- **Cloud backend not connected.** Policy distribution and audit are documented as future work. The local backend doesn't call the cloud.
- **ChatGPT only.** Targets chatgpt.com. Multi-site support (Claude, Gemini) is future work.
- **ONNX not working.** GLiNER2 uses PyTorch mode. ONNX export fails (dynamic control flow). PyTorch is fast enough for the demo.
- **SpanMarker dropped.** PERSON kill test inconclusive (GLiNER2 at 0.953 F1, CI touches 0.97 but can't confirm). Dropped for simplicity.

---

## License

See [LICENSE](LICENSE).

## Team

Graduation project — 5-person team, 4-week development window.

| Role | Responsibility |
|------|---------------|
| Detection engine + evaluation | Tier 1 + Tier 2 + bake-off |
| Vault, security, API | Fernet vault, token auth, endpoints |
| Browser extension | CDP interception, popup UI |
| Routing table + SLM | Policy + adjudicator (stub) |
| Cloud backend + infrastructure | Control plane (future work) |
