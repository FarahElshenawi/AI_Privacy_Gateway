# AI Privacy Gateway

A local privacy layer for LLM web UIs. Intercepts prompts and file uploads in the browser, masks personally identifiable information (PII) before it reaches the LLM, and reversibly restores real values in the response — **no PII ever leaves your machine.**

## How It Works

```
User types prompt / uploads file in ChatGPT
         │
         ▼
  Chrome Extension intercepts the request
         │
         ▼
  Local Backend (FastAPI, 127.0.0.1:8765)
    ├── 1. EXTRACT text (from prompt or file)
    ├── 2. DETECT PII (deterministic tier + semantic NER)
    │         └── Long text is chunked at sentence boundaries 
    ├── 3. MASK (Faker substitution for names/emails; redaction for secrets)
    ├── 4. RESIDUAL SCAN (independent last-gate check — fail-closed)
    └── 5. RECONSTRUCT (write masked text back into original file format)
         │
         ▼
  Masked text/file sent to ChatGPT
         │
         ▼
  ChatGPT responds (with fake values)
         │
         ▼
  ChatGPT responds (with fake values)
         │
         ▼
  DEMASK (backend `/api/demask` exists; NOT yet wired into the extension)
    └── Today the user sees surrogate values in ChatGPT's replies
         │
         ▼
  User sees real values in the ChatGPT UI
```

**PII never crosses the network.** Only masked surrogates are sent to OpenAI. The mapping vault (fake↔real) lives locally and is scoped per-conversation.

### Control Plane (enterprise)

The control plane runs separately and manages policy distribution and audit — it never sees prompt content.

```
┌──────────────────────────────────────────────────────┐
│  CONTROL PLANE (Cloud — cut by default in v1)          │
│                                                        │
│  ┌─────────────────┐     ┌──────────────────────┐     │
│  │  Cloud Backend   │     │  Admin Dashboard      │     │
│  │  (FastAPI)       │     │  (React)               │     │
│  │                  │     │                        │     │
│  │  • Policy CRUD   │◄───►│  • Edit entity rules   │     │
│  │  • Audit ingest  │     │  • View masking stats  │     │
│  │  • Org management│     │  • Leak rate monitor   │     │
│  │  • Signed policy │     │  • Fail-closed events  │     │
│  └────────┬────────┘     └──────────────────────┘     │
│           │                                              │
│           │ (metadata only: counts, types, timing)      │
│           │ NEVER prompt content, masked or unmasked     │
└───────────┼──────────────────────────────────────────────┘
            │
            ▼
   Local Backend pulls policy + pushes audit
```

- **Policy distribution:** entity-type → action mappings (faker/redact/keep) pushed to local backends
- **Audit ingestion:** metadata only (entity types, counts, hashes) — full text requires explicit enterprise opt-in
- **Dashboard:** admin UI for policy configuration and verification statistics


## Quickstart

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 18+ (for extension development)
- Chrome browser

### 1. Clone & Install

```bash
git clone https://github.com/FarahElshenawi/AI_Privacy_Gateway.git
cd AI_Privacy_Gateway
```

### 2. Set up the Local Backend

```bash
cd local-backend

# Install dependencies
uv sync --extra dev

# Start the backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

The backend is now running at `http://127.0.0.1:8765`. Verify:
```bash
curl http://127.0.0.1:8765/health
# → {"status":"ok","service":"pii-gateway-backend","version":"1.0.0"}
```

### 3. Load the Chrome Extension

1. Open `chrome://extensions` in Chrome
2. Enable **Developer Mode** (top right toggle)
3. Click **Load unpacked** → select the `extension/` folder
4. Visit `chatgpt.com` — the extension popup should show a green status dot

### 4. Test It

Type a prompt in ChatGPT containing PII:
> "My name is Farah Ahmed, my card is 4242 4242 4242 4242, email me at farah@example.com"

The extension will:
1. Intercept the prompt before it reaches OpenAI
2. Mask it: "My name is James Walsh, my card is [[REDACTED]], email me at j.walsh@fakermail.com"
3. Send the masked version to ChatGPT
4. (Planned — not yet wired into the extension) Restore real values in the response: "Farah Ahmed", "4242...", "farah@example.com"

## Architecture

### Two-Process Local Design

```
┌─────────────────────────────────────────────────────────────────────┐
│  BROWSER (untrusted page context)                                    │
│  ChatGPT tab ── requests paused at network layer (CDP Fetch)         │
│                           │ chrome.debugger                          │
│                           ┌────────▼──────────┐                     │
│                           │  service-worker   │ (holds token)        │
│                           └────────┬──────────┘                     │
└────────────────────────────────────┼────────────────────────────────┘
                                     │ HTTP (127.0.0.1:8765)
┌────────────────────────────────────┼────────────────────────────────┐
│  LOCAL BACKEND (trusted)            ▼                                 │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Detection Pipeline                                           │   │
│  │  ┌─────────────────┐  ┌──────────────────────────────────┐  │   │
│  │  │ Deterministic   │  │ Semantic (GLiNER2-PII via ONNX)  │  │   │
│  │  │ Luhn, JWT, API   │  │ Names, organizations, locations  │  │   │
│  │  │ keys, IBAN, etc. │  │ + text chunker for long inputs   │  │   │
│  │  └─────────────────┘  └──────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐    │
│  │ Mapping Vault   │  │ Residual Scanner │  │ Multimodal Layer  │    │
│  │ (per-conversation│ │ (independent     │  │ (PDF, Word, Excel,│    │
│  │  bijective, TTL)│  │  last gate)      │  │  text, markdown)  │    │
│  └─────────────────┘  └─────────────────┘  └──────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### Trust Boundaries

- **Browser extension:** Thin forwarder. Holds NO PII. The page context is untrusted.
- **Local backend:** All sensitive processing happens here. Same machine, different process.
- **Cloud:** Cut by default. If present, carries metadata only (counts, timing) — never prompt content.

## Supported PII Types

### Deterministic Tier (~100% precision)

| Type | Detection Method | Masking Action |
|------|-----------------|----------------|
| Credit cards | Luhn checksum | Redact (`[[REDACTED]]`) |
| API keys (AWS, OpenAI, GitHub, Slack, Stripe, Google) | Prefix + length | Redact |
| JWTs | Base64url segment validation | Redact |
| PEM blocks (private keys, certificates) | Header/footer | Redact |
| IBANs | mod-97 checksum | Redact |
| IPv4 / IPv6 | Octet/hex range | Keep |
| Emails | RFC 5322 simplified | Faker substitute |
| Phone (E.164) | `+CC` format | Faker substitute |

### Semantic Tier (GLiNER2-PII)

| Type | Masking Action |
|------|----------------|
| Person names | Faker substitute (realistic fake name) |
| Organizations | Faker substitute (fake company name) |
| Locations | Faker substitute (fake address) |

## Supported File Formats

The multimodal pipeline extracts text, detects PII, and reconstructs files **in place** — preserving original layout, fonts, images, and formatting.

| Format | Parse | Reconstruct | Preservation |
|--------|-------|-------------|-------------|
| Text (`.txt`, `.md`, `.csv`) | ✅ | ✅ | Encoding, BOM, line endings |
| PDF (`.pdf`) | ✅ | ✅ | Layout, images, fonts, vector graphics |
| Word (`.docx`) | ✅ | ✅ | Headers, footers, tables, styles, images |
| Excel (`.xlsx`) | ✅ | ✅ | Formulas, formatting, charts, merged cells |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mask` | POST | Detect + mask PII in text. Returns masked text + pairs. |
| `/api/demask` | POST | Restore real values in LLM response using vault. |
| `/api/detect` | POST | Detect PII only (no masking). Returns entities. |
| `/api/process_file` | POST | Process a file upload (extract → mask → reconstruct). |
| `/health` | GET | Health check (no auth required). |
| `/token` | GET | Get per-install token (for extension setup). |

## Key Design Decisions

1. **Fail-closed:** If the backend is down or the residual scanner finds leaks, the request is blocked. No raw PII ever leaves the device.
2. **Per-conversation vault:** Fake↔real mappings are scoped per conversation. Cross-conversation consistency is a fingerprint, not a feature.
3. **Independent residual scanner:** The last gate re-runs Luhn/JWT/prefix checks on the *masked* output. It does not share code with the primary detector.
4. **Faker over anchors:** Descriptive anchors were rejected after empirical eval (76% re-identifiability vs 14% chance baseline). Faker substitution is strictly safer.
5. **In-place reconstruction:** Files are modified in place (open original → replace text → save). Layout, fonts, images, and formatting are preserved.
6. **Sentence-boundary chunking:** Long documents are split into overlapping chunks at sentence boundaries before semantic inference. This handles GLiNER's 512-token context window without losing entities at boundaries.

See `docs/adr/` for detailed architecture decisions.

## Development

### Run Tests

```bash
cd local-backend
uv run pytest tests/ -v
```

### Project Structure

```
AI_Privacy_Gateway/
├── extension/                 # Chrome extension (Manifest V3)
│   ├── src/background/        # service worker (token, API calls)
│   └── src/popup/             # popup UI
├── local-backend/             # FastAPI local backend
│   ├── app/
│   │   ├── api/               # detect, mask, demask, process_file endpoints
│   │   ├── pipeline/          # detection, masking, routing, residual scanner, chunker
│   │   ├── vault/             # per-conversation bijective vault
│   │   ├── security/          # per-install token, origin check
│   │   └── multimodal/        # file parsers + reconstructors
│   └── tests/                 # 67 tests (deterministic, multimodal, chunker)
├── cloud-backend/             # Cloud control plane (future work)
├── dashboard/                 # Admin dashboard (future work)
├── eval/                      # Hand-built eval set + scoring scripts
├── docs/                      # Architecture, development plan, ADRs
└── infra/                     # Docker compose, VPS service
```

### Pipeline Flow

```
INPUT (text prompt or file)
    │
    ▼
[EXTRACT] — pull text from prompt or file (PDF/Word/Excel/text)
    │
    ▼
[DETECT] — tiered detection engine
    ├── Tier 1: Deterministic (Luhn, JWT, API keys, IBAN, emails, phones)
    │           ~100% precision, no length limit, ~1ms
    └── Tier 2: Semantic (GLiNER2-PII via ONNX)
                ├── TextChunker splits long text at sentence boundaries
                │   (400-token chunks, 50-token overlap, no entity lost)
                ├── GLiNER inference per chunk
                ├── Offset remapping (chunk → full document)
                └── Merge + dedup (overlap zone)
    │
    ▼
[MASK] — routing table decides: Faker (names/emails) or redact (secrets)
    │   outputs (original, replacement) pairs, stores in vault
    ▼
[RESIDUAL SCAN] — independent last gate (re-runs Luhn/JWT/prefix on masked text)
    │   fail-closed: if leaks found, request is blocked
    ▼
[RECONSTRUCT] — apply pairs to original file (in-place, preserves layout)
    │
    ▼
OUTPUT (masked text/file sent to ChatGPT)
```

## Limitations (Stated Honestly)

- **Cannot catch all missed names.** There is no checksum for "this is a person's name." The semantic tier's recall gap is permanent — the residual scanner cannot backstop it.
- **No OCR.** Scanned PDFs and images are rejected (not silently passed through).
- **Cloud backend is a stub.** Policy distribution and audit are documented as future work.
- **ChatGPT only.** v1 targets chatgpt.com. Multi-site support (Claude, Gemini) is future work.

## License

See [LICENSE](LICENSE).
