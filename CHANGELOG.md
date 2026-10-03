# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **CORS for chatgpt.com.** Backend CORS only allowed `chat.openai.com`;
  added `chatgpt.com` to allowed origins.

## [v1.0.0] — Initial release

- MAIN-world `window.fetch` override architecture
- Content scripts for fetch interception + file upload
- Service worker for backend calls + token management
- Multimodal pipeline (PDF, Word, Excel, text)
- Deterministic PII detection (Luhn, JWT, API keys, IBAN, etc.)
- Per-conversation bijective vault
- Independent residual scanner (fail-closed)
- 104 tests passing
