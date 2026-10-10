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
  Replies are restored in the page by a content script (display only):
  surrogates are swapped back to the real values the user typed. Values that are
  redacted without storage (e.g. [REDACTED:CREDIT_CARD]) stay redacted.
```

**PII never crosses the network.** Only masked surrogates are sent to OpenAI.

---

## Architecture

### Two-Process Local Design

```
┌─────────────────────────────────────────────────────────────────────┐
│  BROWSER                                                             │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Service Worker (the enforcement point)                       │    │
│  │  ├── chrome.debugger.attach({tabId}, "1.3")                  │    │
│  │  ├── Fetch.enable (CDP network-layer interception)           │    │
│  │  ├── On Fetch.requestPaused → mask → Fetch.continueRequest   │    │
│  │  ├── per-tab queues kept in chrome.storage.session            │    │
│  │  └── holds the install token                                  │    │
│  └──────────────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Content scripts (convenience only, never the safety net)     │    │
│  │  ├── file-interceptor: masks a picked file before upload      │    │
│  │  └── demask: shows real values in replies (display only)      │    │
│  └──────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                     │ HTTP (127.0.0.1:8765)
┌────────────────────────────────────┼─────────────────────────────────┐
│  LOCAL BACKEND (trusted)            ▼                                 │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  dlp_core — Detection Engine (pure Python)          │   │
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
│  │  ├── api/ (mask, demask, mapping, detect, process_file, policies) │
│  │  ├── pipeline/engine.py (shared singleton)                   │   │
│  │  ├── multimodal/ (PDF, Word, Excel, text handlers + OOXML)   │   │
│  │  └── security/ (token, origin check)                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Trust Boundaries

- **Browser extension service worker:** the enforcement point. It intercepts at the CDP layer — below page scripts, page workers, and page CSP — and blocks anything it cannot mask. Content scripts only add convenience (masking a picked file early, showing real values in replies); nothing depends on them for safety.
- **Local backend:** All sensitive processing happens here. Same machine, different process, bound to `127.0.0.1`. It only answers the pinned extension ID (plus `DLP_EXTENSION_IDS`) and holds the install token. The vault's keys live in the OS credential store.
- **Cloud (optional):** the control plane for a fleet. It carries policies, deny terms, audit **metadata** (counts, timing, entity types) and device status — never prompt or file content. Three separate credentials: an admin key (dashboard), an enrollment key (can only register a device) and a per-device token (heartbeat, audit write, policy read).

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

For a managed install (pinned model, service that restarts at login) see [Deployment](#deployment).

### 2. Load the Chrome Extension

1. Open `chrome://extensions` in Chrome
2. Enable **Developer Mode** (top right toggle)
3. Click **Load unpacked** → select the `extension/` folder
4. Accept the `debugger` permission warning
5. Visit `chatgpt.com` or `gemini.google.com` — a yellow banner appears: "Doppel is debugging this browser"

### 3. Test It

Type a prompt in ChatGPT containing PII:
> "My name is Farah Ahmed, my card is 4242424242424242, email sarah@example.com"

The extension will:
1. Intercept the request via CDP `Fetch.requestPaused`
2. Send to backend for detection + masking
3. Replace the body with masked text
4. Forward the masked request via `Fetch.continueRequest` (base64-encoded)
5. Re-attach itself if Chrome drops the debugger, and keep its per-tab state across service-worker restarts

Attached files (PDF, Word, Excel) are intercepted the same way and sent to `/api/process_file`;
the masked file is what gets uploaded. Anything that can't be masked is blocked.

ChatGPT receives: "My name is [FAKE NAME], my card is [REDACTED:CREDIT_CARD], email [FAKE EMAIL]". In the page you keep seeing your own name and email; the card stays redacted.

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

**Inference mode:** PyTorch only (ONNX export does not work for this model: dynamic control flow).

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
| Word | `handlers/word.py` | Paragraph→run mapping (headers, tables with column-header context, text boxes, hyperlinks, alt text) | Tracked deletions, comments, embedded objects, charts, SmartArt, glossary documents, custom XML text |
| Excel | `handlers/excel.py` | String and numeric cells (with column-header context, e.g. an "SSN" column), formula literals, comments, hidden sheets | Sheet names with PII, charts, custom XML text |
| PDF | `handlers/pdf.py` | Character-level glyph redaction via PyMuPDF | Scans, image-only pages, encrypted files |

File type is decided by **content**, not the extension (a zip holding `word/document.xml` is Word; a renamed file is not trusted).

Word and Excel output is also **scrubbed of metadata**: thumbnails, reviewer lists, custom and application properties, and author names are removed or blanked. Images cannot be read, so `DLP_IMAGE_POLICY` decides what happens to them (blocked by default); parts the scanner cannot read (charts, SmartArt) are blocked unless `DLP_OOXML_PARTS=warn`, and even then a raw scan of every package part still blocks card numbers, keys and similar hard evidence.

**Fail-closed rules:**
- Scanned/image-only PDFs and standalone images → rejected (no OCR)
- Failed critical detector → no output file
- Masked file re-read and residual-scanned
- `strict=True` blocks when coverage is incomplete; for files this is on by default (`DLP_FILE_STRICT`), so a file is never returned while Tier 2 was down

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mask` | POST | Detect + mask PII. Returns `masked_text`, `entity_types`, `safe_to_send`, `degraded`, `coverage_complete`, `uncovered_labels`. |
| `/api/demask` | POST | Restore real values in a text using the vault (audited, rate-limited). |
| `/api/mapping` | POST | Versioned fake→real entries of one conversation, used by the extension to restore replies in the page (audited, rate-limited). |
| `/api/detect` | POST | Detect PII only (no masking). Returns spans with offsets. |
| `/api/process_file` | POST | Process a file upload (extract → mask → reconstruct → re-read and verify). 50 MB cap. Refusals are 4xx with the reason and blocker codes. |
| `/api/policies/active`, `/apply`, `/reset` | GET / POST | Inspect or change the live label → action table (critical secrets can never be set to `keep`). |
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
| `DLP_TIER1_TIMEOUT_S` / `DLP_TIER2_TIMEOUT_S` | 2.0 / 3.0 | Base time budget per tier. Tier 1 is critical (exceeding blocks); Tier 2 degrades. Big inputs get more time per 1000 characters (`DLP_TIERn_PER_KCHAR_S`), up to `DLP_TIERn_MAX_S` for prompts and `DLP_TIERn_FILE_MAX_S` for files. |
| `DLP_TIER2_ENABLED` | true | Enable/disable GLiNER2 |
| `DLP_TIER2_MODEL` | model id | Model id or a local directory, normally the pinned snapshot from `scripts/fetch_model.py`. |
| `DLP_WARM_TIER2` | true | Pre-load model at startup in background thread |
| `DLP_STRICT` | false | Block prompts when coverage is incomplete (production: set true) |
| `DLP_FILE_STRICT` | true | Refuse files when any detector tier was degraded |
| `DLP_MAX_TEXT_CHARS` | 200000 | Max text length for `/mask` and `/detect` |
| `DLP_IMAGE_POLICY` / `DLP_OOXML_PARTS` | default / block | What to do with images and with unscannable Word/Excel parts |
| `DLP_EXTENSION_IDS` | unset | Extra extension IDs the backend accepts (the pinned ID is always allowed) |
| `DLP_VAULT_*`, `DLP_KEYSTORE`, `CLOUD_URL`, `CLOUD_ENROLL_KEY` | see below | Vault persistence and keys, cloud sync |

The full table, with the vault, cloud-sync and threshold settings, is in [`local-backend/README.md`](local-backend/README.md).

----------|---------|-------------|
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

Tests that need the real gliner2 model are skipped when it is not installed.

Extension: `cd extension && npm test` (mocked `chrome.*` and CDP, the real service worker). Dashboard: `cd dashboard && npx tsc --noEmit && npm run build`. Cloud: `cd cloud-backend && pytest` (PostgreSQL tests run when `TEST_POSTGRES_URL` is set). CI runs all of these, the local backend on Linux, Windows and macOS.

Areas covered: merge/masking/vault invariants, Tier 1 and Tier 2, the detection pipeline, the evaluation harness and
frozen hold-out hash, the API, security (token, origin, pinned extension ID), multimodal files, cloud sync and audit events.
Run `pytest dlp_core tests` in `local-backend/` and `pytest` in `cloud-backend/`; per-file counts are not listed here because they drift.


---

## Deployment

For a fleet, `installer/` has one installer per OS (service that starts at login and restarts on failure, listening on `127.0.0.1` only). The Tier 2 model is downloaded once at a **pinned commit** by `scripts/fetch_model.py` and loaded from disk, so it cannot change underneath a deployment. The extension is force-installed by Chrome policy, which can also lock protection on (`protectionLocked`).

See [`docs/deployment.md`](docs/deployment.md) for the commands, the Chrome policy JSON, update handling and post-rollout checks. The extension ID is pinned (`efcejekkbkbbknfpjgbpkojgnoomggbi`); keep `doppel-extension.pem` secret, because whoever holds it can publish an update Chrome will accept as Doppel.

---

## Evaluation

### Tier 1 baseline (frozen hold-out, 298 cases)

> Numbers are from the last full evaluation run. The hold-out file itself is not committed (it contains synthetic secrets that GitHub push protection rejects); regenerate it with `python -m dlp_core.eval.make_holdout`, which is checked against `holdout_v1.sha256` by a test.

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
│   ├── dlp_core/                # Pure Python detection engine 
│   │   ├── span.py              # Span contract (start, end, label, score, source, validated)
│   │   ├── policy.py            # Label → Action (unknown = REDACT, fail-closed)
│   │   ├── merge.py             # MergeEngine (union overlaps, strictest wins)
│   │   ├── masker.py            # OffsetMasker (offset-based, no str.replace) + Demasker
│   │   ├── vault.py             # Vault (Fernet-sealed, HMAC-keyed); PersistentVault = encrypted SQLite
│   │   ├── keystore.py          # OS credential store for the vault keys (file fallback)
│   │   ├── detection.py         # DetectionPipeline (parallel tiers, timeouts, degraded)
│   │   ├── segments.py          # SegmentMasker (per-segment offset edits for files)
│   │   ├── residual_scanner.py # Independent last-gate (checksums only, fail-closed)
│   │   ├── tier1/               # Deterministic (regex + validators) — 24 entity types
│   │   ├── tier2/               # Semantic (GLiNER2-PII) — 17 labels
│   │   └── eval/                # Eval harness + bake-off + frozen hold-outs
│   ├── app/                     # FastAPI layer (thin wrappers)
│   │   ├── api/                 # mask, demask, mapping, detect, process_file, policies
│   │   ├── pipeline/engine.py   # Shared singleton wiring
│   │   ├── cloud_sync.py        # Enroll, heartbeat, audit events, policy + tenant config pull
│   │   ├── multimodal/          # File pipeline, content-based type detection, OOXML scrubbing
│   │   │   └── handlers/        # Per-format handlers (PDF, Word, Excel, text)
│   │   └── security/            # Token, origin check
│   └── tests/                   # Integration tests
├── extension/                   # Chrome extension (Manifest V3, chrome.debugger)
│   ├── manifest.json            # debugger permission, host_permissions
│   ├── managed_schema.json      # Enterprise policy: protectionLocked
│   ├── src/background/          # CDP interception, file/text masking calls, state.js
│   ├── src/content/             # file-interceptor, demask (display only)
│   ├── tests/                   # node --test (mock chrome/CDP)
│   └── src/popup/               # Popup UI (stats, attach status)
├── cloud-backend/               # Cloud control plane: policies, audit, tenant config, devices (Alembic, PostgreSQL-ready)
├── dashboard/                   # Admin dashboard: activity, policies + history, devices, detection settings, admin log
├── installer/                   # Windows / macOS / Linux service installers for the local backend
├── scripts/                     # fetch_model.py (pinned Tier 2 model), dev setup
├── eval/                        # Evaluation sets + scoring scripts
├── docs/                        # Architecture, ADRs, deployment guide
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

- **Response demasking is display-only.** The extension restores real values in the ChatGPT/Gemini page (via `/api/mapping`) as replies stream in; values that are not stored for restoring (e.g. `[REDACTED:CREDIT_CARD]`) stay redacted, and the restored text lives in the page's DOM.
- **No OCR.** Scanned PDFs and standalone images are rejected (fail-closed). Images inside Word/Excel are not read, so their content is not masked; `DLP_IMAGE_POLICY=block` refuses such files. Charts and SmartArt are blocked rather than read.
- **Legacy Office files** (`.doc`, `.xls`) are not supported and are refused.
- **Chrome shows a "debugging" bar** while Doppel is attached; that is the cost of network-layer interception.
- **Not yet verified here:** real Tier 2 speed on your hardware, the installers on Windows/macOS, and a real Word/Excel file with charts or SmartArt. A signed installer package needs your code-signing certificates.
- **Cloud sync is opt-in.** With `CLOUD_URL` and `CLOUD_ENROLL_KEY` set, the local backend enrolls, heartbeats, pushes audit metadata and pulls policies using a per-device token; the admin key stays with the dashboard.
- **ChatGPT and Gemini only.** Other sites (Claude, etc.) are future work.
- **No ONNX.** GLiNER2 runs in PyTorch; measure speed on your hardware with `benchmark_tier2.py`.
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
| Routing table + policy | Per-label actions, cloud policy sync |
| Cloud backend + infrastructure | Control plane (policies, audit, tenant config, devices) and dashboard |
