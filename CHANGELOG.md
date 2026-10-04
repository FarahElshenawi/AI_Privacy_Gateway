# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.2.0] — 2026-10-04

### Added
- **Gemini (gemini.google.com) support.**
  - Prompts: `StreamGenerate` form posts are parsed (`f.req` → inner JSON), the prompt text and attached
    file names are masked, and the body is rebuilt with every other field (`at`, flags, conversation ids) intact.
  - File uploads: single `multipart/form-data` uploads to `content-push.googleapis.com` /
    `push.clients6.google.com` have the file bytes masked via `/api/process_file`.
  - **Fail-closed:** unrecognised request shapes and resumable-protocol uploads that carry raw file bytes
    (`x-goog-upload-command: upload…`) are blocked, and the popup Diagnostics shows the content-type/command seen.
- Popup **Diagnostics** panel: attach status and the last 25 intercepted requests with outcomes (no content).
- ChatGPT: blob-storage upload host, `sk-proj-…` key detection, periodic re-attach (alarm) so tabs are never
  silently unprotected after a service-worker sleep or extension reload.

### Caveats
- Gemini support is built from the publicly reverse-engineered request format and verified against a local
  mock in Chromium, **not against the live site**. If Gemini changes its format, requests are blocked, not leaked.
- Person names (e.g. in filenames) are still not detected until the semantic detector is implemented.

## [v2.1.0] — 2026-10-04

Hardening release. Fixes privacy leaks and fail-open paths found in review of v2.0.0.

### Security / privacy
- **Entity types no longer leak real values.** The activity log was built from `pairs[0]`
  (the *original* values). Types now come from the new `entity_types` field on `/api/mask`.
- **No user content in logs.** Removed console logging of message text, bodies, filenames.
- **Popup renders activity with escaping** (previously raw `innerHTML`).
- **Filenames are masked** in upload reservations and message attachment metadata.

### Fail-closed
- Any body-retrieval failure, backend error/timeout, residual leak, or unexpected exception on a
  send / file request now **blocks** the request (v2.0.0 passed some of these through).
- Rewritten bodies are **verified**: if any original value from `pairs` is still present, block.
- Each user message part is masked and written back **by assignment** (no `String.replace`,
  so `$&` etc. in masked text are literal and multi-part messages are handled).
- Backend calls have timeouts.

### Fixed
- Body retrieval uses `postDataEntries` / `networkId` + `Network.getRequestPostData`
  (honouring `base64Encoded`) instead of URL-keyed lookups and base64 guessing.
- Send endpoint match is exact; `/f/conversation/prepare` and similar are not touched.
- Filename for file uploads comes from the (masked) upload reservation, with magic-byte sniffing as fallback.
- Protection toggle is honoured (detaches/attaches immediately); SW restarts re-adopt debugger sessions.
- Stable per-conversation vault id so the same real value gets the same surrogate within a chat.
- `/api/process_file` reads `conversation_id` from the multipart form (it was a query param).
- PDF reconstructor now applies the supplied `pairs` (it was hard-coded to replace "farah" → "hager").
- Extension version/popup footer aligned; stats are no longer reset on extension update.

### Removed
- Dead content scripts (`src/content/*`), empty `src/lib/masking.js`, unused `faker` dependency.

### Corrections to earlier entries
- v2.0.0 claimed the backend CORS allow-list gained `chatgpt.com`. It did not (and should not: the
  extension reaches the backend via `host_permissions`, and `/token` is unauthenticated).
- Response demasking is **not implemented in the extension**; users see surrogate values in ChatGPT's
  replies. The `/api/demask` endpoint exists in the backend only.

## [v2.0.0] — 2026-10-03

### Changed — Architecture overhaul: MAIN-world fetch override → chrome.debugger + CDP

The browser extension was completely rebuilt. The v1 approach monkey-patched
`window.fetch` in the page's MAIN world; v2 uses the `chrome.debugger` API +
Chrome DevTools Protocol (CDP) `Fetch.enable` command to intercept requests
at the network layer.

#### Why we abandoned v1

After six iterations of patching, four architectural faults remained unfixable:

1. **ChatGPT bypassed our hook** via Service Workers and pre-cached `fetch`
   references captured before our content script ran
2. **React hydration broke** when our badge `<div>` was injected into the
   page DOM during `document_start` (React error #418)
3. **The MAIN↔isolated↔SW bridge** was racy across three async boundaries,
   and MV3 SWs get killed at random
4. **Body-stream surgery on `Request` objects** corrupted binary file uploads
   ("Request object that has already been used" error)

#### What v2 does instead

- `chrome.debugger.attach({tabId}, "1.3")` on chatgpt.com tabs
- `Fetch.enable` with URL patterns for `/backend-api/conversation*` and `*.oaiusercontent.com/*`
- `Network.enable` for body retrieval (Chrome 136+ removed `Fetch.getRequestPostData`)
- On `Fetch.requestPaused` event: read body, call local backend `/api/mask`, rewrite body, `Fetch.continueRequest`

### Added

- **File upload interception.** ChatGPT uploads file bytes via PUT to
  `https://*.oaiusercontent.com/files/{uuid}/raw?sig=...` (Azure-backed, Cloudflare-fronted).
  v2 intercepts this PUT, calls `/api/process_file`, replaces the body with masked bytes.
- **Three-step ChatGPT upload flow support:**
  1. `POST /backend-api/files/upload_reservations` (metadata only — pass through)
  2. `PUT https://*.oaiusercontent.com/...` (actual file bytes — mask here)
  3. `POST /backend-api/files/upload_reservations/{id}/claim_and_finish` (pass through)
- **Network domain fallback.** When Chrome 136+ removed `Fetch.getRequestPostData`,
  v2 falls back to `Network.getRequestPostData` using a URL→requestId map
  populated from `Network.requestWillBeSent` events.
- **Base64 body encoding for CDP.** `Fetch.continueRequest` expects `postData`
  as base64, not plain string. Added `utf8ToBase64` / `utf8ToBase64FromBytes`
  helpers (handles full Unicode safely, not just Latin1).
- **Popup debugger status.** Shows which tabs are attached, with manual
  attach/detach button.
- **Better filename detection.** The PUT URL only has `/files/{uuid}/raw` —
  no filename. Derives extension from `Content-Type` header
  (`application/pdf` → `.pdf`, etc.).

### Removed

- **All content scripts deleted.** `fetch-override.js`, `file-upload-override.js`,
  `content-script.js`, `inject.js` — no longer needed. Interception happens in
  the SW via CDP, not in the page.
- **MAIN↔isolated↔SW postMessage bridge.** No longer needed; the SW is the
  only actor.
- **On-page badge DOM injection.** Removed — it was breaking React hydration.
  Status now shown in the popup only.

### Fixed (vs v1)

- **Snake_case field names.** Backend returns `safe_to_send`, `masked_text`,
  `entities_found` (Python snake_case). v1 was checking `safeToSend`,
  `maskedText`, `entitiesFound` (JS camelCase). Fixed.
- **ChatGPT request body shape.** ChatGPT uses `msg.author.role` (nested),
  not `msg.role`. v1 was checking `msg.role` and never matched. v2 handles
  both shapes via `getUserRole()` helper.
- **Path filter.** v1's pattern `/backend-api/conversation` was too broad —
  it intercepted metadata calls like `/conversation/init`, `/f/conversation/prepare`,
  `/conversations?offset=...` which have no user text. v2 narrows to the
  actual send endpoint (`/backend-api/conversation` or `/backend-api/f/conversation`
  exactly, no trailing slash) and passes through everything else.
- **Infinite postMessage echo.** v1's content-script listener accepted ANY
  message with the tag — including its OWN responses. Caused 50+ postMessage
  calls per second. v2 filters: only forward REQUESTS (with a `type` field),
  never RESPONSES (with a `response` field).

## [v1.0.0] — Initial release

- MAIN-world `window.fetch` override architecture
- Content scripts for fetch interception + file upload
- Service worker for backend calls + token management
- Multimodal pipeline (PDF, Word, Excel, text)
- Deterministic PII detection (Luhn, JWT, API keys, IBAN, etc.)
- Per-conversation bijective vault
- Independent residual scanner (fail-closed)
- 104 tests passing
