# Extension — Data Plane, Enforcement Point 1

Chrome extension (Manifest V3). Owns the MAIN-world `fetch()` override on chatgpt.com —
the highest-variance component in the system (see docs/development-plan.md, Week 1, Role 3).

**Security note (docs/architecture.md 1.4):** this code runs in the page's JS context,
which is untrusted. It must never hold real PII or the Vault — it only forwards prompts
to the Local Backend and swaps in the masked body it gets back.

## Layout

```
manifest.json
src/
  background/service-worker.js   — owns the install token; the only part that talks to the backend
  content/content-script.js      — ISOLATED world; bridges MAIN world <-> service worker
  content/fetch-override.js      — MAIN world; conversation text masking/demasking
  content/file-upload-override.js — MAIN world; file upload masking (shares a registry with fetch-override.js)
  lib/message-text.js            — pure helpers (no DOM/chrome.*), unit tested under Node
  lib/sse-demask.js              — pure rolling-buffer SSE demask, unit tested under Node
  popup/                         — status, escape hatch toggle, stats, audit log
tests/                           — node --test (`npm test`), runs the real src/ files
icons/
```

## Running the tests

```
npm test
```

Runs `node --test tests/*.test.js` (Node 18+, no install needed — Node's
built-in test runner and Web Streams API are used directly). Covers:

- `message-text.test.js` — request-body extraction/rewrite, pure logic.
- `sse-demask.test.js` — the rolling-buffer demask, including the
  split-token and length-drift regressions it exists to prevent.
- `service-worker.test.js` — the backend response field-name contract
  (see "Fixed in this pass" below).
- `fetch-override.test.js` — integration test of the real
  `fetch-override.js` (loaded via `vm`, with a stubbed message relay):
  escape hatch, fail-closed blocking, mask-success rewrite.

## Escape hatch

"Protection Active" in the popup is the one sanctioned way to send a
message unmasked. Turning it off does **not** silently bypass masking —
every toggle and every request sent while it's off is written to the
audit log (visible in the popup, under "Audit Log") and counted in the
"Escape-hatch bypasses" stat. Fail-closed blocks (backend down, residual
leak detected, unexpected error) are logged the same way and also show a
banner on the page itself, so a block is attributable to the extension
rather than looking like a generic network error.

## Fixed in this pass (2026-10-02)

- **Critical:** `service-worker.js`'s `maskText()` was spreading the
  backend's snake_case JSON response (`masked_text`, `safe_to_send`,
  `entities_found`) directly into the result, while every caller read
  camelCase fields (`maskedText`, `safeToSend`, `entitiesFound`). Those
  were always `undefined`, so `!maskResult.safeToSend` was always `true`
  and **every prompt was blocked as a false "residual leak."** Fixed to
  map the fields explicitly, matching the pattern `demaskText()` already
  used correctly.
- Implemented the logged escape hatch + audit log + stats end to end
  (previously the popup's toggle wrote to storage but nothing ever read
  it, and the stat tiles were permanently stuck at 0).
- Hardened against page re-wrapping: `window.fetch` is rebuilt from a
  captured native reference on a 500ms check, and a shared middleware
  registry (`window.__piiGatewayRegister`) replaces the old pattern of
  two scripts independently reassigning `window.fetch` — which depended
  on dynamically-injected `<script>` load order that the HTML spec does
  not actually guarantee.
- Rewrote the SSE demask buffer: the old version cut at a fixed
  character count and discarded the buffer on each flush, which could
  split a masked token in half (leaking the fake value un-restored) and
  which emitted most frames to the page still showing masked/fake text.
  The new version (`src/lib/sse-demask.js`) never finalizes a chunk
  until it's provably safe, and emits a running diff against its own
  prior output rather than reusing offsets from the masked text (which
  drift whenever a replacement's length differs from the original's).
- Removed duplicate file-upload handling (it existed independently in
  both `fetch-override.js` and `file-upload-override.js`).
- Removed dead code: `inject.js` (unreferenced), `src/lib/masking.js`
  (unimplemented stub — masking is server-side, see
  `local-backend/app/pipeline/masking.py`), and the `faker@6.6.6` npm
  dependency (unused, and that exact version is the 2022 sabotaged
  Faker.js release).
- Added icons, bumped manifest to 0.2.0.
- `local-backend/app/pipeline/masking.py` (also Role 3-owned per
  CODEOWNERS): fixed an entity whose type isn't in the routing table
  being silently left **unmasked** (the fail-safe default should be
  redact, per the Week-1 GUIDE's documented contract — a previous
  version did the opposite); fixed the same real value not reusing its
  fake across separate messages in one conversation. Added
  `local-backend/tests/test_masking.py` (31 tests — none existed before).
