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

  // --- Inject the MAIN-world scripts, in a guaranteed order ---
  //
  // Both scripts cooperate through a shared registry (see the top-of-file
  // comment in fetch-override.js) so correctness no longer depends on
  // which one runs first. But per the HTML spec, a dynamically-created
  // classic <script> defaults to async=true, meaning the browser is free
  // to execute two such scripts in EITHER order regardless of append
  // order. Setting `.async = false` on both restores "fetch in parallel,
  // execute in document order" — the standard trick for deterministic
  // ordering of dynamically inserted scripts — which is still worth doing
  // for clarity even though the registry no longer strictly requires it.
  function injectMainWorldScript(path) {
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL(path);
    script.async = false;
    script.onload = function () {
      this.remove();
    };
    (document.head || document.documentElement).appendChild(script);
  }

  // The lib/ scripts (pure logic, also unit-tested under Node — see
  // extension/tests/) must land before fetch-override.js, which reads
  // window.PIIGatewayMessageText / window.PIIGatewaySSEDemask at the top
  // of its own IIFE.
  injectMainWorldScript("src/lib/message-text.js");
  injectMainWorldScript("src/lib/sse-demask.js");
  injectMainWorldScript("src/content/fetch-override.js");
  injectMainWorldScript("src/content/file-upload-override.js");

  // --- Message bridge: MAIN world ↔ service worker ---

  // Unique tag so we don't mix up our messages with page's own postMessage traffic
  const MSG_TAG = "__PII_GATEWAY__";

  window.addEventListener("message", async function (event) {
    // Only accept messages from the same window
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.tag !== MSG_TAG) return;

    // This listener's OWN replies (posted below as
    // `window.postMessage({ tag: MSG_TAG, id, response })`) are
    // themselves "message" events on window, tagged with the same
    // MSG_TAG — so without this check, this same listener re-catches
    // every reply it just sent and treats it as a brand-new request: it
    // reads `data.type`/`data.payload` off a reply object (which has
    // neither — a reply has `response`, not `type`), forwards
    // `{ type: undefined, payload: undefined }` to the service worker,
    // gets back an "Unknown: undefined" error, and posts THAT back to
    // the page using the SAME id as the real request. On a quiet page
    // this extra bounce is just waste; under real load (lots of the
    // page's own fetches/messages happening at once, as seen live on
    // chatgpt.com) it was indistinguishable from — and a likely
    // contributor to — the real response going missing (fetch-override.js
    // reporting "Masking failed: No response" despite the backend having
    // already returned 200 OK). A reply object never has `type`; a
    // request always does — that's the distinguishing signal.
    if (data.type === undefined) return;

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
