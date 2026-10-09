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

## Replies: real values shown in the page

The model only sees fake values, so its answers contain them. `src/content/demask.js` (ChatGPT
and Gemini) restores your real values in the text you see, as it streams in: it asks the service
worker for this conversation's fake→real entries (`/api/mapping` on the local backend, versioned
so an unchanged conversation costs one tiny call every 2 s), and a MutationObserver rewrites the
text nodes that contain a known fake. Replacement is longest-fake-first with word boundaries —
the same rule as the backend's `/api/demask`. Because it works on rendered text, streaming and
reloaded chat history both work. The composer is never touched.

The popup switch **Show real values in replies** turns it off (new text only; reload to see the
fakes again). Honest limit: the restored values are written into the page's DOM, so the site's
own scripts can technically read them. `[REDACTED:TYPE]` placeholders for values that are not
stored for restoring (e.g. card numbers) stay as they are.

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

- Replies are restored in the page only (not on the wire); `[REDACTED:TYPE]` values that are not stored stay redacted.
- Images and scanned PDFs are blocked (no OCR).
- ChatGPT/Gemini change request formats without notice; re-verify on real traffic after updates.

## Layout

```
manifest.json
src/background/service-worker.js   # CDP interception, classification, backend calls
src/background/body.js             # pure helpers: body parsing/building, file sniffing
src/content/file-interceptor.js    # ChatGPT: swap attached files for masked ones (picker/drop/paste)
src/content/demask.js              # ChatGPT + Gemini: show real values in replies
src/popup/                         # stats, attach status, diagnostics
tests/                             # body.test.js, sw.test.js, content.test.js, demask.test.js
```

## Pinned extension ID

`manifest.json` carries a `key`, so the extension ID is always `efcejekkbkbbknfpjgbpkojgnoomggbi`
(unpacked, force-installed, any machine). The local backend only accepts requests whose Origin is
that ID (`origin_check.PINNED_EXTENSION_ID`; a test checks the two stay in sync). If you publish
under a different ID (e.g. the Chrome Web Store), set `DLP_EXTENSION_IDS=<id>[,<id>]` on the backend.
The matching private key is only needed to sign packages; keep it out of the repository.
