// MAIN-world fetch() override.
// Runs inside the untrusted page context (docs/architecture.md, 1.4 — Execution Boundary).
//
// Rules for this file:
//   1. Never store real PII or Vault data here. Forward-only.
//   2. Every outbound request body is sent to the Local Backend for masking
//      before it leaves the browser.
//   3. On Local Backend failure: FAIL CLOSED. Block the request and show the
//      escape-hatch UI (see docs/architecture.md, 1.4 — Fail-Closed Policy).
//
// TODO(Role 3, Week 1 Day 1 spike): confirm this successfully intercepts
// chatgpt.com's request shape before building anything on top of it.

(function () {
  const originalFetch = window.fetch;

  window.fetch = async function (...args) {
    // TODO: detect outbound chat-completion requests, extract prompt body
    // TODO: POST prompt to http://localhost:8765/mask with per-install token
    // TODO: on backend failure -> block + show escape hatch (fail-closed)
    // TODO: replace request body with masked version before calling originalFetch
    return originalFetch.apply(this, args);
  };
})();
