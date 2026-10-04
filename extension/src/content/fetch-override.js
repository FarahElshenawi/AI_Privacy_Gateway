/**
 * MAIN-world fetch() override.
 *
 * Runs inside the untrusted page context. Rules:
 *   1. Never store real PII or Vault data here. Forward-only.
 *   2. Every outbound request body is sent to the Local Backend for masking
 *      before it leaves the browser.
 *   3. On Local Backend failure: FAIL CLOSED. Block the request.
 *   4. The one sanctioned way around rule 3 is the "escape hatch" — the
 *      popup's Protection toggle. Every single request sent while it's off
 *      is logged to the audit log via logEvent()/bumpStat() below. It is
 *      never a silent bypass.
 *
 * Ownership note: file uploads (FormData to FILE_UPLOAD_PATTERNS) are
 * handled exclusively by file-upload-override.js, not here — via the
 * shared middleware registry defined below. An earlier version of this
 * file AND file-upload-override.js each independently captured and
 * overwrote window.fetch. That's broken in a way that's easy to miss:
 * content-script.js injects both as separate dynamically-created <script>
 * elements, and per the HTML spec a dynamically-inserted classic script
 * defaults to async=true, so the browser does NOT guarantee they execute
 * in the order they were appended. Whichever happened to run second would
 * wrap whichever was "window.fetch" at that moment — usually, but not
 * reliably, the other one's wrapper — and file uploads were in practice
 * being passed through BOTH independent copies of the FormData-handling
 * code. See content-script.js for the matching fix (script.async = false
 * on both injected scripts, to make load order deterministic) — but this
 * registry removes the dependency on load order entirely, which is the
 * more durable fix of the two.
 *
 * --- Shared middleware registry (window.__piiGatewayWrappers) ---
 * Both this file and file-upload-override.js run in the same MAIN-world
 * `window`, so they can cooperate through one shared, ordered pipeline
 * instead of each independently reassigning window.fetch:
 *   1. The native fetch is captured exactly once, the first time either
 *      script runs (window.__piiGatewayNativeFetch).
 *   2. Each script registers a middleware — (next) => (thisArg,input,init)
 *      => Promise<Response> — instead of touching window.fetch directly.
 *   3. window.fetch is always (re)built by composing every registered
 *      middleware around the native fetch, and marked with
 *      __piiGatewayInstalled so the periodic re-wrap check (below) can
 *      tell "this is still our composed chain" from "the page replaced
 *      it," and rebuild on top of the *native* fetch either way — so a
 *      page-level hijack can never end up as the innermost link.
 */

(function () {
  "use strict";

  if (!window.__piiGatewayNativeFetch) {
    window.__piiGatewayNativeFetch = window.fetch;
  }
  window.__piiGatewayWrappers = window.__piiGatewayWrappers || [];

  // --- Self-healing window.fetch (fixes a real bug seen live on
  // chatgpt.com: masking silently never ran for any request) ---
  //
  // An earlier version of this file just did `window.fetch = composed;`
  // once, then polled every 500ms to notice if the page had reassigned
  // window.fetch and reinstall our override. That's a genuine race, and
  // on the current chatgpt.com it loses: chatgpt.com's own bundle
  // reassigns window.fetch during its own startup, and whatever code
  // reads window.fetch right after doing that reassignment (to build
  // its own instrumented fetch, store a reference for later use, etc.)
  // can run well within that 500ms window — getting back ITS OWN
  // replacement, not ours. From that point on, every real conversation
  // request goes through a reference our later poll-triggered rebuild
  // can never reach, because reassigning window.fetch again doesn't
  // retroactively change a reference some other closure already
  // captured. Symptom in practice: the extension loads fine, logs
  // "Fetch interceptor loaded", even logs "window.fetch was reassigned
  // by the page — reinstalling override" once — and then masks
  // precisely zero prompts, forever, because the page's own code
  // stopped reading window.fetch at all after that point.
  //
  // Fix: don't poll. Make `window.fetch` a getter/setter pair instead of
  // a plain property. The getter always returns OUR current composed
  // chain. The setter treats whatever the page tries to assign as a new
  // "native" layer to wrap and rebuilds immediately, synchronously, in
  // the same statement the page used to reassign it — so there is no
  // gap in which someone can read window.fetch and get anything other
  // than a PII-Gateway-wrapped function, no matter how many times or
  // how soon after our own setup the page reassigns it.
  let composedFetch = null;

  function piiGatewayRebuild(newNative) {
    if (newNative && newNative !== composedFetch) {
      window.__piiGatewayNativeFetch = newNative;
    }
    const native = window.__piiGatewayNativeFetch;
    let chain = (thisArg, input, init) => native.call(thisArg, input, init);
    for (const { middleware } of window.__piiGatewayWrappers) {
      chain = middleware(chain);
    }
    const composed = function (input, init) {
      return chain(this, input, init);
    };
    composed.__piiGatewayInstalled = true;
    composedFetch = composed;
    return composed;
  }

  try {
    Object.defineProperty(window, "fetch", {
      configurable: true,
      enumerable: true,
      get() {
        return composedFetch;
      },
      set(value) {
        // The page setting window.fetch back to exactly what we just
        // handed it (e.g. `window.fetch = window.fetch`) isn't a real
        // reassignment — ignore it rather than rebuilding for no reason.
        if (value === composedFetch) return;
        piiGatewayRebuild(value);
      },
    });
  } catch (err) {
    // Some environment makes window.fetch non-configurable — fall back
    // to a plain assignment plus the old polling safety net below
    // rather than throwing and disabling masking entirely.
    console.error("[PII Gateway] Could not install self-healing fetch property:", err);
  }

  piiGatewayRebuild(window.__piiGatewayNativeFetch);

  // Exposed so file-upload-override.js (a separate script/closure) can
  // register its own middleware into the same pipeline.
  window.__piiGatewayRegister = function (name, middleware) {
    if (window.__piiGatewayWrappers.some((w) => w.name === name)) return;
    window.__piiGatewayWrappers.push({ name, middleware });
    piiGatewayRebuild();
  };

  window.__piiGatewayRebuild = piiGatewayRebuild;

  const MSG_TAG = "__PII_GATEWAY__";
  let messageId = 0;

  // Conversation endpoints (ChatGPT sends prompts here)
  const CONVERSATION_PATTERNS = [
    "/backend-api/conversation",
    "/backend-api/f/conversation",
  ];

  // --- Helper: send message to service worker via content script ---

  function sendToBackgroundOnce(type, payload) {
    return new Promise((resolve) => {
      const id = ++messageId;
      let timeoutId;

      function handler(event) {
        if (event.source !== window) return;
        const data = event.data;
        if (!data || data.tag !== MSG_TAG || data.id !== id) return;
        // Ignore our OWN outgoing request: window.postMessage delivers it
        // to this same window too, with the same tag and id. Only a reply
        // (which carries `response`, and no `type`) may resolve this call.
        if (data.type !== undefined || !("response" in data)) return;
        window.removeEventListener("message", handler);
        clearTimeout(timeoutId); // the response arrived — the fail-closed fallback below is no longer needed
        if (data.response && data.response.success) {
          resolve(data.response);
        } else {
          resolve(data.response || { success: false, error: "No response" });
        }
      }

      window.addEventListener("message", handler);
      window.postMessage({ tag: MSG_TAG, id, type, payload }, "*");

      // Timeout after 30 seconds — fail closed
      timeoutId = setTimeout(() => {
        window.removeEventListener("message", handler);
        resolve({ success: false, error: "Backend timeout (30s)" });
      }, 30000);
    });
  }

  // One retry specifically for "No response" (as opposed to a real
  // backend-reported failure like "Backend 503" or a validation error).
  // "No response" means the round trip through content-script.js and the
  // MV3 service worker completed with nothing usable — observed live on
  // chatgpt.com even with the backend logging a clean 200 OK for the
  // same /api/mask call, i.e. a dropped reply somewhere in the
  // extension's own messaging, not a real masking failure. That's worth
  // one immediate retry before this surfaces as a fail-closed block,
  // since the backend demonstrably already did the work correctly the
  // first time. A genuine failure (the backend actually erroring, or a
  // real timeout) is NOT retried here — retrying those would just waste
  // 30 more seconds reproducing the same real failure.
  async function sendToBackground(type, payload) {
    const first = await sendToBackgroundOnce(type, payload);
    if (first.success || first.error !== "No response") return first;
    console.warn(`[PII Gateway] "${type}" got no response — retrying once before failing closed.`);
    return sendToBackgroundOnce(type, payload);
  }

  // --- Helpers: audit log + stats (fire-and-forget; never block on these) ---

  function logEvent(event, details) {
    sendToBackground("LOG_EVENT", { event, details }).catch(() => {});
  }

  function bumpStat(key, amount = 1) {
    sendToBackground("INCREMENT_STAT", { key, amount }).catch(() => {});
  }

  // --- Fail-closed UI: a visible, dismissible banner on the page itself ---
  //
  // Previously a block just returned an opaque Response with a JSON error
  // body, which ChatGPT's own UI had no way to render meaningfully (the
  // user would see a generic "something went wrong"/network-error state
  // with no indication PII Gateway was involved). This renders a small
  // fixed banner directly on the page so a block is visibly attributable
  // to the extension, not a mystery failure.

  function showFailClosedBanner(reason) {
    try {
      const EXISTING_ID = "__pii_gateway_banner__";
      let el = document.getElementById(EXISTING_ID);
      if (!el) {
        el = document.createElement("div");
        el.id = EXISTING_ID;
        el.style.cssText = [
          "position:fixed", "top:0", "left:0", "right:0", "z-index:2147483647",
          "background:#b3261e", "color:#fff", "font-family:system-ui,sans-serif",
          "font-size:13px", "padding:8px 14px", "text-align:center",
          "box-shadow:0 2px 6px rgba(0,0,0,.3)",
        ].join(";");
        document.documentElement.appendChild(el);
      }
      el.textContent = `PII Gateway blocked this message (fail-closed): ${reason}`;
      clearTimeout(el._hideTimer);
      el._hideTimer = setTimeout(() => el.remove(), 8000);
    } catch {
      // DOM not ready / unavailable — the console.error below is the fallback.
    }
  }

  // --- Pure logic lives in src/lib/ so it can be unit tested without a
  // browser (see extension/tests/). Loaded as plain globals ahead of this
  // file — see content-script.js for the injection order.
  const { matchesPattern, extractMessageText, replaceMessageText } = window.PIIGatewayMessageText;
  const { createDemaskingStream: createDemaskingStreamImpl } = window.PIIGatewaySSEDemask;

  function createDemaskingStream(responseBody, conversationId) {
    return createDemaskingStreamImpl(responseBody, (text) =>
      sendToBackground("DEMASK", { text, conversationId })
    );
  }

  // --- The actual masking/blocking logic for one conversation request ---
  // `next(thisArg, input, init)` continues the shared middleware chain
  // (ultimately reaching the native fetch) — see the registry docs above.

  async function handleConversationRequest(next, thisArg, input, init, url) {
    const conversationId = "conv_" + Date.now();
    const originalText = extractMessageText(init.body);

    if (!originalText || !originalText.trim()) {
      return next(thisArg, input, init);
    }

    // --- Escape hatch check ---
    // Protection can be switched off from the popup. When it is, we do NOT
    // silently let traffic through — every bypassed request is logged and
    // counted, and a visible (non-blocking) banner says so on the page.
    const protectionState = await sendToBackground("GET_PROTECTION_ENABLED", {});
    if (protectionState.success && protectionState.enabled === false) {
      console.warn("[PII Gateway] Protection is OFF — sending UNMASKED (escape hatch).");
      logEvent("ESCAPE_HATCH_BYPASS", { url, conversationId, textLength: originalText.length });
      bumpStat("bypassCount");
      showFailClosedBanner("protection is OFF — this message was sent unmasked");
      return next(thisArg, input, init);
    }

    try {
      console.log("[PII Gateway] Intercepted prompt, masking...");

      const maskResult = await sendToBackground("MASK", {
        text: originalText,
        conversationId: conversationId,
      });

      if (!maskResult.success) {
        console.error("[PII Gateway] Masking failed:", maskResult.error);
        logEvent("BLOCKED_MASK_FAILURE", { url, conversationId, error: maskResult.error });
        bumpStat("blocksCount");
        showFailClosedBanner("the local backend could not be reached");
        return new Response(
          JSON.stringify({
            error: "PII Gateway: masking failed — request blocked (fail-closed)",
            detail: maskResult.error,
          }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        );
      }

      if (!maskResult.safeToSend) {
        console.error("[PII Gateway] Residual scanner found leaks — blocking");
        logEvent("BLOCKED_RESIDUAL_LEAK", { url, conversationId, leaks: maskResult.leaks });
        bumpStat("blocksCount");
        showFailClosedBanner("the residual scanner found a possible leak");
        return new Response(
          JSON.stringify({
            error: "PII Gateway: residual scanner detected leaks — request blocked",
            detail: maskResult.leaks,
          }),
          { status: 422, headers: { "Content-Type": "application/json" } }
        );
      }

      init.body = replaceMessageText(init.body, originalText, maskResult.maskedText);

      console.log(`[PII Gateway] Prompt masked (${maskResult.entitiesFound} entities). Forwarding.`);
      bumpStat("promptsMasked");
      if (maskResult.entitiesFound) bumpStat("entitiesMasked", maskResult.entitiesFound);

      const response = await next(thisArg, input, init);

      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("text/event-stream") && response.body) {
        const demaskedStream = createDemaskingStream(response.body, conversationId);
        return new Response(demaskedStream, {
          headers: response.headers,
          status: response.status,
          statusText: response.statusText,
        });
      }

      if (response.body) {
        const text = await response.text();
        const demaskResult = await sendToBackground("DEMASK", { text, conversationId });
        const restoredText =
          demaskResult.success && demaskResult.restoredText ? demaskResult.restoredText : text;
        return new Response(restoredText, {
          headers: response.headers,
          status: response.status,
          statusText: response.statusText,
        });
      }

      return response;
    } catch (err) {
      console.error("[PII Gateway] Interception error:", err);
      logEvent("BLOCKED_EXCEPTION", { url, conversationId, error: err.message });
      bumpStat("blocksCount");
      showFailClosedBanner("an unexpected error occurred");
      return new Response(
        JSON.stringify({
          error: "PII Gateway: interception error — request blocked",
          detail: err.message,
        }),
        { status: 503, headers: { "Content-Type": "application/json" } }
      );
    }
  }

  // --- Register our middleware into the shared pipeline ---
  //
  // Hardening against page re-wrapping / early DOM states (GUIDE.md, Tue):
  //   1. window.__piiGatewayNativeFetch is captured once, at the top of
  //      this file (document_start, before any page script runs), and
  //      piiGatewayRebuild() always composes middleware around *that*
  //      reference — never whatever window.fetch happens to be at the
  //      time of rebuild — so even if the page has replaced window.fetch,
  //      rebuilding puts our chain back on top of the real native fetch,
  //      not on top of a page-controlled shim that could silently drop
  //      our masking.
  //   2. A recurring check notices if something other than our own
  //      composed function is sitting on window.fetch (the page's own
  //      code reassigning it, or a timing accident where a page script
  //      ran before this content script's injected <script> executed)
  //      and calls piiGatewayRebuild() to reassert it.
  //   3. __piiGatewayInstalled marks the composed function so the
  //      recurring check can tell "still ours" from "replaced," without
  //      needlessly rebuilding (and without ever double-wrapping).

  window.__piiGatewayRegister("conversation-mask", (next) => {
    return function (thisArg, input, init) {
      const url = typeof input === "string" ? input : input?.url || "";
      if (matchesPattern(url, CONVERSATION_PATTERNS) && init && init.body) {
        return handleConversationRequest(next, thisArg, input, init, url);
      }
      return next(thisArg, input, init);
    };
  });

  // Last-resort fallback only: with the Object.defineProperty trap above
  // in place, window.fetch can no longer be silently replaced out from
  // under us — any attempted reassignment is caught synchronously by the
  // setter. This poll now only matters in the one case that trap can't
  // cover (window.fetch was non-configurable, so defineProperty threw
  // above and we're sitting on a plain property instead).
  setInterval(() => {
    if (!window.fetch || !window.fetch.__piiGatewayInstalled) {
      console.warn("[PII Gateway] window.fetch was reassigned by the page — reinstalling override.");
      logEvent("FETCH_REWRAPPED_BY_PAGE", {});
      window.__piiGatewayRebuild();
    }
  }, 500);

  console.log("[PII Gateway] Fetch interceptor loaded (MAIN world)");
})();
