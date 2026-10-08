# Extension — Enforcement Point 1

Chrome extension (Manifest V3) that masks sensitive data **before it leaves the browser** on
ChatGPT and Gemini, by rewriting network requests with the local backend's output.

## How it works

The service worker attaches `chrome.debugger` to ChatGPT/Gemini tabs and uses the Chrome
DevTools Protocol `Fetch` domain to pause matching requests *below* page scripts, workers and
CSP. There are no content scripts and no DOM injection; the page never sees real PII flow
through code it controls.

For each paused request the worker classifies it, reads the full body, sends the sensitive
parts to the local backend (`http://127.0.0.1:8765`), and continues the request with the
masked body.

| Site | Intercepted request | What is masked |
|------|---------------------|----------------|
| ChatGPT | `/backend-api/f/conversation` | prompt text, attachment names |
| ChatGPT | `/f/conversation/prepare` | draft text |
| ChatGPT | `/backend-api/files` + blob `PUT` | file name; file bytes (PDF, Word, Excel) |
| Gemini | `StreamGenerate` (`f.req`) | prompt text |
| Gemini | resumable upload / multipart upload | file name; file bytes (PDF, Word, Excel) |

Text goes to `/api/mask`; files go to `/api/process_file`. File type is detected from the
bytes (PDF magic, `word/document.xml` / `xl/workbook.xml` inside the ZIP), not from the name.

## Fail closed

If anything goes wrong — body can't be read, backend unreachable or rejects the token,
backend reports `safe_to_send: false`, unsupported file (image, scanned PDF, unknown type),
file over 50 MB — the request is **blocked** (`Fetch.failRequest`), never forwarded unmasked.
Degraded coverage (e.g. the name model still warming up) is surfaced in the popup.

## Backend auth

The worker fetches the per-install token from `GET /token` and sends it as
`Authorization: Bearer <token>`. On a `401` it drops the cached token and retries once. The
local backend only accepts requests from extension origins.

## Install (development)

1. Start the local backend (see [`local-backend/README.md`](../local-backend/README.md)).
2. `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select this folder.
3. Accept the debugger warning; open chatgpt.com or gemini.google.com. Chrome shows
   "Doppel is debugging this browser" while attached.

## Test

```bash
cd extension
npm test        # node --test; mocked chrome + CDP, fake backend mirroring the real contract
npm run lint
```

## Limitations

- Responses are **not demasked** yet: replies show the fake/`[REDACTED:…]` values.
- Images and scanned PDFs are blocked (no OCR).
- ChatGPT/Gemini change request formats without notice; re-verify on real traffic after updates.

## Layout

```
manifest.json
src/background/service-worker.js   # CDP interception, classification, backend calls
src/background/body.js             # pure helpers: body parsing/building, file sniffing
src/popup/                         # stats, attach status, diagnostics
tests/                             # body.test.js, sw.test.js
```
