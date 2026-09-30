/**
 * Content script (isolated world) — the message bridge.
 *
 * This runs in Chrome's isolated world. It can talk to:
 *   - The MAIN world (via window.postMessage)
 *   - The service worker (via chrome.runtime.sendMessage)
 *
 * It also injects the fetch-override.js into the MAIN world so it can
 * intercept page-level fetch() calls.
 */

(function () {
  "use strict";

  // --- Inject the MAIN-world fetch override ---
  const script = document.createElement("script");
  script.src = chrome.runtime.getURL("src/content/fetch-override.js");
  script.onload = function () {
    this.remove();
  };
  (document.head || document.documentElement).appendChild(script);

  // Also inject the file-upload override
  const fileScript = document.createElement("script");
  fileScript.src = chrome.runtime.getURL("src/content/file-upload-override.js");
  fileScript.onload = function () {
    this.remove();
  };
  (document.head || document.documentElement).appendChild(fileScript);

  // --- Message bridge: MAIN world ↔ service worker ---

  // Unique tag so we don't mix up our messages with page's own postMessage traffic
  const MSG_TAG = "__PII_GATEWAY__";

  window.addEventListener("message", async function (event) {
    // Only accept messages from the same window
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.tag !== MSG_TAG) return;

    // Forward to service worker and wait for response
    try {
      const response = await chrome.runtime.sendMessage({
        type: data.type,
        payload: data.payload,
      });
      // Send response back to MAIN world
      window.postMessage({ tag: MSG_TAG, id: data.id, response: response }, "*");
    } catch (err) {
      // Service worker unavailable — fail closed
      window.postMessage(
        { tag: MSG_TAG, id: data.id, response: { success: false, error: err.message } },
        "*"
      );
    }
  });

  console.log("[PII Gateway] Content script bridge loaded");
})();
