// Handles the per-install token handshake with the Local Backend
// (docs/architecture.md 1.4 — Local Backend Security).
// Token is generated on first launch and stored via chrome.storage.local,
// never hardcoded in the bundle.

chrome.runtime.onInstalled.addListener(async () => {
  // TODO: generate per-install token, POST to localhost:8765/handshake,
  // persist to chrome.storage.local
});
