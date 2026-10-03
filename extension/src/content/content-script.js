/**
 * Content script (isolated world) — the message bridge. v2.
 *
 * FIX vs v1:
 *   v1 had an infinite postMessage echo. The listener accepted ANY message
 *   with the MSG_TAG — including its OWN responses. Each response was then
 *   re-forwarded to the service worker as {type:undefined, payload:undefined},
 *   which the SW answered with {success:false, error:"Unknown: undefined"},
 *   which was then forwarded back to the page, which was then received again...
 *   infinite loop. Symptoms: 50+ postMessage calls per second, CPU spikes,
 *   and `chrome.runtime.sendMessage` returning undefined on real requests
 *   because the SW was drowning in junk messages.
 *
 *   v2 filters: only forward REQUESTS (messages with a `type` field),
 *   never RESPONSES (messages with a `response` field).
 */

(function () {
  "use strict";

  const MSG_TAG = "__PII_GATEWAY__";
  let handled = 0;

  window.addEventListener("message", async function (event) {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.tag !== MSG_TAG) return;

    // CRITICAL: only forward REQUESTS (with a `type` field).
    // Skip RESPONSES (with a `response` field) — those are coming back from
    // us and must not be re-processed, or we get an infinite postMessage loop.
    if (data.type === undefined) return;
    if (data.response !== undefined) return;

    handled++;
    console.log(`[PII Gateway] bridge #${data.id} (${data.type}) — forwarding to SW`);

    try {
      const response = await chrome.runtime.sendMessage({
        type: data.type,
        payload: data.payload,
        id: data.id,
      });

      if (response === undefined) {
        // chrome.runtime.sendMessage returns undefined when:
        //   - No listener is registered (SW failed to load)
        //   - Listener didn't call sendResponse
        //   - SW was killed mid-call (MV3 service worker lifecycle)
        console.error(`[PII Gateway] bridge #${data.id}: SW returned undefined — service worker may be dead`);
        window.postMessage(
          { tag: MSG_TAG, id: data.id, response: {
            success: false,
            error: "Service worker did not respond (returned undefined). Open chrome://extensions → click 'Inspect views: service worker' to check for errors.",
          } },
          "*"
        );
        return;
      }

      window.postMessage(
        { tag: MSG_TAG, id: data.id, response: response },
        "*"
      );
    } catch (err) {
      console.error(`[PII Gateway] bridge #${data.id}: error`, err.message);
      window.postMessage(
        { tag: MSG_TAG, id: data.id, response: { success: false, error: err.message } },
        "*"
      );
    }
  });

  console.log("[PII Gateway] Content script bridge v2 loaded (isolated world)");
})();
