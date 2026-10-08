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

## Files: swapped in the page, then verified on the network

ChatGPT uploads files as blobs, which the debugger can't always read reliably. So on
chatgpt.com a small content script (`src/content/file-interceptor.js`) replaces each attached
file with its **masked** version before the page's own code sees it. It covers the file picker,
drag-and-drop and paste. The file goes to the local backend through the service worker; if any
file in a batch can't be masked, none is attached and a red notice explains why.

The network layer still checks the upload: a request whose body matches a file the extension
just masked (by SHA-256, single use) is passed through; anything else is masked or blocked as
below. Gemini uses the network path only (no content script yet).

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
src/content/file-interceptor.js    # ChatGPT: swap attached files for masked ones (picker/drop/paste)
src/popup/                         # stats, attach status, diagnostics
tests/                             # body.test.js, sw.test.js, content.test.js
```
