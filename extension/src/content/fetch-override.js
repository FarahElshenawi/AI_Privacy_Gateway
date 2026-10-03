/**
 * MAIN-world fetch() + XHR override — v6.
 *
 * v6 fixes vs v5 (CRITICAL):
 *
 *   Bug #1 — "Request object that has already been used" crash
 *     v5 called `await readBody(input.body)` when input was a Request.
 *     This CONSUMES the body stream. The original Request becomes "disturbed"
 *     and can never be sent. ChatGPT then crashes with:
 *       RequestError: Failed to execute 'fetch' on 'Window': Cannot construct
 *       a Request with a Request object that has already been used.
 *     v6 uses `input.clone().text()` to read from a CLONE — the original
 *     Request is never touched, and can be passed straight to originalFetch.
 *
 *   Bug #2 — Crash on `extractMessageText` returning null
 *     v5 returned `{passed: true}` after consuming the Request body, but
 *     by then the original Request was already broken. v6 fixes the root
 *     cause (clone before read), so pass-through now actually passes through
 *     unchanged.
 *
 *   Bug #3 — SW message-listener returning `true` instead of `Promise`
 *     In MV3, the SW `onMessage` listener must return `true` to keep the
 *     channel open for async `sendResponse`. v5 returned `true` correctly
 *     but the PING case had no `await` so the function returned synchronously,
 *     closing the channel before sendResponse fired. v6 wraps in async IIFE
 *     and returns true explicitly (already correct, but verified).
 *
 *   Bug #4 — Wrong endpoint patterns
 *     v6 narrows mask interception to the SINGLE actual send endpoint:
 *       POST /backend-api/conversation       ← real message send (has .messages)
 *     Other paths like /conversation/init, /f/conversation/prepare,
 *     /conversations?offset=... are NOT sends — they're metadata fetches
 *     and should pass through untouched.
 */

(function () {
  "use strict";

  if (window.__PII_GATEWAY_FETCH_HOOKED__) {
    console.log("[PII Gateway] v6 already installed — skipping");
    return;
  }
  window.__PII_GATEWAY_FETCH_HOOKED__ = true;

  const MSG_TAG = "__PII_GATEWAY__";
  let messageId = 0;

  // ONLY the actual message-send endpoint carries a .messages array.
  // /conversation/init and /f/conversation/prepare are metadata calls
  // and must NOT be intercepted — they have no user text to mask.
  const CONVERSATION_SEND_PATTERN = "/backend-api/conversation";

  // Patterns where the response may contain tokens we need to demask.
  // Wider — any /backend-api/conversation* response might stream tokens.
  const DEMASK_RESPONSE_PATTERNS = [
    "/backend-api/conversation",
    "/backend-api/f/conversation",
  ];

  const FILE_UPLOAD_PATTERNS = ["/backend-api/files", "/backend-api/upload"];

  const BACKEND_URL = "http://127.0.0.1:8765";

  const debugLog = [];
  function dbg(level, msg, data) {
    const entry = { t: new Date().toISOString().slice(11, 23), level, msg, data };
    debugLog.push(entry);
    if (debugLog.length > 100) debugLog.shift();
    if (level === "error") console.error("[PII Gateway]", msg, data ?? "");
    else console.log("[PII Gateway]", msg, data ?? "");
  }

  // ─────────────────────────────────────────────────────────
  // Badge
  // ─────────────────────────────────────────────────────────
  let badgeHost, badgeRoot, badge;
  const badgeStats = { intercepted: 0, masked: 0, blocked: 0, lastError: null };

  function buildBadge() {
    badgeHost = document.createElement("div");
    badgeHost.id = "__pii_gateway_host__";
    badgeHost.style.cssText = [
      "position:fixed",
      "bottom:16px",
      "right:16px",
      "z-index:2147483647",
      "display:block",
      "max-width:360px",
    ].join(";");

    badgeRoot = badgeHost.attachShadow({ mode: "open" });
    badgeRoot.innerHTML = `
      <style>
        .wrap {
          background: #0d1117; color: #c9d1d9;
          border: 2px solid #30363d; border-radius: 10px;
          padding: 8px 12px;
          font: 12px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          box-shadow: 0 6px 20px rgba(0,0,0,.5);
          cursor: pointer; min-width: 220px; word-break: break-word;
        }
        .wrap:hover { background: #161b22; }
        .wrap.ok   { border-color: #238636; color: #3fb950; }
        .wrap.warn { border-color: #9e6a03; color: #f0b429; }
        .wrap.err  { border-color: #da3633; color: #f85149; animation: pulse 1s infinite; }
        @keyframes pulse {
          0%, 100% { box-shadow: 0 6px 20px rgba(218,54,51,.5); }
          50% { box-shadow: 0 6px 30px rgba(218,54,51,.9); }
        }
        .title { font-weight: 600; font-size: 11px; opacity: .7; text-transform: uppercase; letter-spacing: .5px; }
        .body  { font-size: 12px; margin-top: 2px; }
        .hint  { font-size: 10px; opacity: .5; margin-top: 4px; }
        button {
          margin-top: 6px; padding: 3px 8px;
          background: #21262d; color: #c9d1d9;
          border: 1px solid #30363d; border-radius: 4px;
          font: 10px/1.4 monospace; cursor: pointer; display: block;
        }
        button:hover { background: #30363d; }
      </style>
      <div class="wrap" id="b">
        <div class="title">PII GATEWAY v6</div>
        <div class="body" id="body">loaded · waiting</div>
        <button id="test">test backend</button>
        <button id="dump">dump debug log</button>
        <div class="hint">click badge to dump</div>
      </div>
    `;
    badge = badgeRoot.getElementById("b");
    const testBtn = badgeRoot.getElementById("test");
    const dumpBtn = badgeRoot.getElementById("dump");

    badge.addEventListener("click", (e) => {
      if (e.target === testBtn || e.target === dumpBtn) return;
      dumpDebug();
    });
    testBtn.addEventListener("click", (e) => { e.stopPropagation(); runBackendTest(); });
    dumpBtn.addEventListener("click", (e) => { e.stopPropagation(); dumpDebug(); });
  }

  function attachBadge() {
    if (!badgeHost) buildBadge();
    if (badgeHost.parentNode) return;
    if (document.body) {
      document.body.appendChild(badgeHost);
      dbg("info", "Badge attached");
    } else {
      setTimeout(attachBadge, 50);
    }
  }

  const observer = new MutationObserver(() => {
    if (badgeHost && !badgeHost.parentNode && document.body) {
      dbg("warn", "Badge was removed — re-attaching");
      document.body.appendChild(badgeHost);
    }
  });

  function setBadge(text, kind) {
    attachBadge();
    const body = badgeRoot.getElementById("body");
    if (body) body.textContent = text;
    if (badge) badge.className = "wrap" + (kind ? " " + kind : "");
  }

  function dumpDebug() {
    console.log("%c[PII Gateway] === DEBUG REPORT ===", "color:#58a6ff;font-weight:bold");
    console.log("Stats:", badgeStats);
    for (const e of debugLog) {
      const prefix = `%c[${e.t}] ${e.level.toUpperCase()}`;
      const style = e.level === "error" ? "color:#f85149" :
                    e.level === "warn"  ? "color:#f0b429" : "color:#58a6ff";
      console.log(prefix, style, e.msg, e.data ?? "");
    }
    console.log("%c=== END REPORT ===", "color:#58a6ff;font-weight:bold");
    alert("[PII Gateway] v6 Debug report dumped to console (F12 → Console).\nStats: " + JSON.stringify(badgeStats, null, 2));
  }

  async function runBackendTest() {
    setBadge("running tests…", "warn");
    dbg("info", "=== Running backend self-test ===");

    // Test 1: PING service worker (no network, no dependencies)
    try {
      dbg("info", "Test 1: PING service worker");
      const r = await sendToBackground("PING", {}, { timeoutMs: 5000 });
      dbg("info", "Test 1 result:", r);
      if (!r.success) {
        setBadge("❌ SW dead — see console", "err");
        dumpDebug();
        return;
      }
    } catch (e) {
      dbg("error", `Test 1 FAILED: ${e.message}`);
      setBadge("❌ bridge broken", "err");
      dumpDebug();
      return;
    }

    // Test 2: bridge HEALTH
    try {
      dbg("info", "Test 2: bridge HEALTH");
      const r = await sendToBackground("HEALTH", {}, { timeoutMs: 10000 });
      dbg("info", "Test 2 result:", r);
    } catch (e) {
      dbg("error", `Test 2 FAILED: ${e.message}`);
    }

    // Test 3: bridge MASK with "hello"
    try {
      dbg("info", "Test 3: bridge MASK 'hello'");
      const r = await sendToBackground("MASK",
        { text: "hello", conversationId: "test_" + Date.now() },
        { timeoutMs: 15000 }
      );
      dbg("info", "Test 3 result:", r);
    } catch (e) {
      dbg("error", `Test 3 FAILED: ${e.message}`);
    }

    setBadge("test done — see console (F12)", "ok");
    dumpDebug();
  }

  // ─────────────────────────────────────────────────────────
  // Bridge to service worker
  // ─────────────────────────────────────────────────────────
  function sendToBackground(type, payload, { timeoutMs = 30000 } = {}) {
    return new Promise((resolve) => {
      const id = ++messageId;
      dbg("info", `→ bridge send #${id} type=${type}`);
      function handler(event) {
        if (event.source !== window) return;
        const data = event.data;
        if (!data || data.tag !== MSG_TAG || data.id !== id) return;
        window.removeEventListener("message", handler);
        dbg("info", `← bridge recv #${id}:`, data.response);
        resolve(data.response || { success: false, error: "No response" });
      }
      window.addEventListener("message", handler);
      window.postMessage({ tag: MSG_TAG, id, type, payload }, "*");
      setTimeout(() => {
        window.removeEventListener("message", handler);
        const err = `Bridge timeout (${timeoutMs / 1000}s)`;
        dbg("error", `← bridge TIMEOUT #${id}: ${err}`);
        resolve({ success: false, error: err });
      }, timeoutMs);
    });
  }

  // ─────────────────────────────────────────────────────────
  // SAFE body reading — uses clone() for Request objects
  // ─────────────────────────────────────────────────────────

  /**
   * Read body WITHOUT disturbing the original Request.
   *
   * If body is a string/Blob/ArrayBuffer — read it directly (no disturbance).
   *
   * If body is on a Request (input.body), we must NOT read input.body
   * directly — that would lock the stream and break ChatGPT's downstream
   * fetch call. Instead, we read input.clone().text(), which:
   *   1. Creates a clone of the Request (clones share body state safely)
   *   2. Reads the body of the CLONE as text
   *   3. The original Request remains usable for originalFetch.call(this, input, init)
   *
   * This is THE critical fix that v5 was missing.
   */
  async function readBodySafely(input, init) {
    // Case A: body is on init.body (string/Blob/FormData/ReadableStream)
    if (init && init.body != null) {
      return { bodyStr: await readBody(init.body), bodyKind: "init.body", input, init };
    }

    // Case B: body is on a Request object
    if (input && typeof input === "object" && input instanceof Request) {
      // CLONE before reading — otherwise we disturb the original Request
      const cloned = input.clone();
      const bodyStr = await cloned.text();
      return { bodyStr, bodyKind: "Request.body", input, init };
    }

    // Case C: no body
    return { bodyStr: null, bodyKind: "none", input, init };
  }

  async function readBody(body) {
    if (body == null) return null;
    if (typeof body === "string") return body;
    if (body instanceof Blob) return await body.text();
    if (body instanceof ArrayBuffer) return new TextDecoder().decode(body);
    if (body instanceof Uint8Array) return new TextDecoder().decode(body);
    if (body instanceof ReadableStream) {
      const reader = body.getReader();
      const decoder = new TextDecoder();
      let out = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        out += decoder.decode(value, { stream: true });
      }
      return out;
    }
    if (body instanceof FormData) {
      const obj = {};
      for (const [k, v] of body.entries()) {
        obj[k] = v instanceof File ? `__FILE:${v.name}__` : v;
      }
      return JSON.stringify(obj);
    }
    try { return String(body); } catch { return null; }
  }

  function getUserRole(msg) {
    if (!msg) return null;
    if (msg.role) return msg.role;
    if (msg.author && msg.author.role) return msg.author.role;
    return null;
  }

  function extractMessageText(bodyStr) {
    try {
      const parsed = JSON.parse(bodyStr);
      if (!parsed.messages || !Array.isArray(parsed.messages)) return null;
      const texts = [];
      for (const msg of parsed.messages) {
        const role = getUserRole(msg);
        if (role !== "user") continue;
        const content = msg.content;
        if (!content) continue;
        if (Array.isArray(content.parts)) {
          for (const part of content.parts) {
            if (typeof part === "string") texts.push(part);
            else if (part && typeof part === "object" && typeof part.text === "string") texts.push(part.text);
          }
        } else if (typeof content.text === "string") {
          texts.push(content.text);
        }
      }
      if (texts.length === 0) return null;
      return texts.join("\n");
    } catch { return null; }
  }

  function replaceMessageText(bodyStr, originalText, maskedText) {
    try {
      const parsed = JSON.parse(bodyStr);
      if (!parsed.messages || !Array.isArray(parsed.messages)) return bodyStr;
      for (const msg of parsed.messages) {
        const role = getUserRole(msg);
        if (role !== "user") continue;
        const content = msg.content;
        if (!content) continue;
        if (Array.isArray(content.parts)) {
          for (let i = 0; i < content.parts.length; i++) {
            if (typeof content.parts[i] === "string") {
              content.parts[i] = content.parts[i].replace(originalText, maskedText);
            } else if (content.parts[i] && typeof content.parts[i] === "object" && typeof content.parts[i].text === "string") {
              content.parts[i].text = content.parts[i].text.replace(originalText, maskedText);
            }
          }
        } else if (typeof content.text === "string") {
          content.text = content.text.replace(originalText, maskedText);
        }
      }
      return JSON.stringify(parsed);
    } catch (e) {
      dbg("error", "replaceMessageText failed:", e.message);
      return bodyStr;
    }
  }

  function createDemaskingStream(responseBody, conversationId) {
    const reader = responseBody.getReader();
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let buffer = "";
    let demaskBuffer = "";
    const demaskThreshold = 100;

    return new ReadableStream({
      async pull(controller) {
        try {
          const { done, value } = await reader.read();
          if (done) {
            if (demaskBuffer) {
              const r = await sendToBackground("DEMASK", { text: demaskBuffer, conversationId });
              if (r.success && r.restoredText) controller.enqueue(encoder.encode(r.restoredText));
              else controller.enqueue(encoder.encode(demaskBuffer));
            }
            controller.close();
            return;
          }
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const data = line.slice(6).trim();
              if (data === "[DONE]") { controller.enqueue(encoder.encode("data: [DONE]\n")); continue; }
              try {
                const json = JSON.parse(data);
                let text = "";
                if (json.v && json.v.message && json.v.message.content) text = json.v.message.content.parts?.[0] || "";
                else if (json.message && json.message.content) text = json.message.content.parts?.[0] || "";
                if (text) {
                  demaskBuffer += text;
                  if (demaskBuffer.length >= demaskThreshold) {
                    const r = await sendToBackground("DEMASK", { text: demaskBuffer, conversationId });
                    if (r.success && r.restoredText) {
                      if (json.v) { if (json.v.message?.content?.parts) json.v.message.content.parts[0] = r.restoredText; }
                      else if (json.message?.content?.parts) json.message.content.parts[0] = r.restoredText;
                    }
                    demaskBuffer = "";
                  }
                  controller.enqueue(encoder.encode(`data: ${JSON.stringify(json)}\n`));
                } else {
                  controller.enqueue(encoder.encode(`data: ${data}\n`));
                }
              } catch {
                controller.enqueue(encoder.encode(`data: ${data}\n`));
              }
            } else {
              controller.enqueue(encoder.encode(line + "\n"));
            }
          }
        } catch (err) { controller.error(err); }
      },
      cancel() { reader.cancel(); },
    });
  }

  // ─────────────────────────────────────────────────────────
  // Main: maybe mask conversation
  // ─────────────────────────────────────────────────────────
  async function maybeMaskConversation(url, input, init) {
    // CRITICAL: read body WITHOUT disturbing the original Request
    const { bodyStr, bodyKind } = await readBodySafely(input, init);

    if (!bodyStr) {
      // No body — pass through unchanged (original input/init are untouched)
      return { passed: true };
    }

    // Try to extract user text — if there's no .messages array, this returns null
    // and we pass through without modifying anything.
    const originalText = extractMessageText(bodyStr);
    if (!originalText || !originalText.trim()) {
      // Pass through — original input/init are untouched, safe to send as-is
      return { passed: true };
    }

    dbg("info", "User text (first 200):", originalText.slice(0, 200));
    dbg("info", "Body kind:", bodyKind, "length:", bodyStr.length);
    badgeStats.intercepted++;
    setBadge(`intercepting… (#${badgeStats.intercepted})`, "warn");

    const conversationId = "conv_" + Date.now();
    const maskResult = await sendToBackground("MASK", { text: originalText, conversationId });

    dbg("info", "Mask result:", {
      success: maskResult.success,
      safe_to_send: maskResult.safe_to_send,
      entities_found: maskResult.entities_found,
      error: maskResult.error,
    });

    if (!maskResult.success) {
      badgeStats.blocked++;
      badgeStats.lastError = maskResult.error;
      const errShort = (maskResult.error || "unknown").slice(0, 80);
      setBadge(`❌ BLOCKED: ${errShort}`, "err");
      return {
        passed: false,
        response: new Response(
          JSON.stringify({
            error: "PII Gateway: masking failed — request blocked (fail-closed)",
            detail: maskResult.error,
            hint: "Click the PII Gateway badge (bottom-right) → 'test backend' for diagnostics.",
          }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        ),
      };
    }

    if (!maskResult.safe_to_send) {
      badgeStats.blocked++;
      badgeStats.lastError = "residual leak detected";
      setBadge(`❌ BLOCKED: residual leak`, "err");
      return {
        passed: false,
        response: new Response(
          JSON.stringify({
            error: "PII Gateway: residual scanner detected leaks — request blocked",
            detail: maskResult.leaks,
          }),
          { status: 422, headers: { "Content-Type": "application/json" } }
        ),
      };
    }

    const newBodyStr = replaceMessageText(bodyStr, originalText, maskResult.masked_text);
    badgeStats.masked++;
    setBadge(
      `✓ masked ${maskResult.entities_found} entities (total ${badgeStats.masked})`,
      "ok"
    );
    dbg("info", `Masked (${maskResult.entities_found} entities). New body first 200:`, newBodyStr.slice(0, 200));

    // Write the masked body back to wherever the original body lived.
    // CRITICAL: when bodyKind is "Request.body", we MUST construct a NEW Request
    // with the masked body — the original Request's body stream has already been
    // consumed by our clone. (Don't worry: we cloned, so the original is still
    // usable — but its body is the original unmasked text, which we don't want
    // to send. We construct a fresh Request with the masked body.)
    if (bodyKind === "init.body") {
      init.body = newBodyStr;
      return { passed: true, input, init };
    } else {
      // bodyKind === "Request.body" — construct a fresh Request
      // Pull all the properties off the original input, replace the body.
      const newReq = new Request(input, {
        method: input.method || "POST",
        headers: input.headers,
        body: newBodyStr,
        mode: input.mode,
        credentials: input.credentials,
        cache: input.cache,
        redirect: input.redirect,
        referrer: input.referrer,
        integrity: input.integrity,
      });
      return { passed: true, input: newReq, init: undefined };
    }
  }

  // ─────────────────────────────────────────────────────────
  // Fetch override
  // ─────────────────────────────────────────────────────────
  const originalFetch = window.fetch;

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input?.url || "";

    // Only intercept the actual message-send endpoint.
    // Skip /conversation/init, /f/conversation/prepare, /conversations?offset=... etc.
    const isSendEndpoint =
      url.includes(CONVERSATION_SEND_PATTERN) &&
      !url.includes("/conversation/init") &&
      !url.includes("/conversations?") &&
      !url.includes("/f/conversation/prepare");

    if (isSendEndpoint) {
      try {
        const result = await maybeMaskConversation(url, input, init);
        if (!result.passed) return result.response;
        // Use the (possibly modified) input/init from the result
        if (result.input !== input || result.init !== init) {
          input = result.input;
          init = result.init;
        }
      } catch (err) {
        dbg("error", "Fetch interception error:", err.message);
        setBadge(`❌ ERROR: ${err.message}`, "err");
        return new Response(
          JSON.stringify({ error: "PII Gateway: interception error — request blocked", detail: err.message }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        );
      }
    }

    if (FILE_UPLOAD_PATTERNS.some((p) => url.includes(p)) && init && init.body instanceof FormData) {
      try {
        const formData = init.body;
        const file = formData.get("file");
        if (file && file instanceof File) {
          dbg("info", "File upload:", file.name);
          const fileResult = await sendToBackground("PROCESS_FILE",
            { file: file, filename: file.name, contentType: file.type },
            { timeoutMs: 60000 });
          if (!fileResult.success) {
            return new Response(
              JSON.stringify({ error: "PII Gateway: file masking failed — upload blocked", detail: fileResult.error }),
              { status: 503, headers: { "Content-Type": "application/json" } }
            );
          }
          if (fileResult.maskedFileBlob) {
            const newFormData = new FormData();
            for (const [k, v] of formData.entries()) {
              if (k === "file") newFormData.append("file", fileResult.maskedFileBlob, file.name);
              else newFormData.append(k, v);
            }
            init.body = newFormData;
          }
        }
      } catch (err) {
        dbg("error", "File upload error:", err.message);
        return new Response(
          JSON.stringify({ error: "PII Gateway: file interception error — upload blocked", detail: err.message }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        );
      }
    }

    const response = await originalFetch.call(this, input, init);

    // Demask responses for ALL conversation-pattern URLs (send + prepare + init
    // all stream responses that may contain tokens). The demask call is a no-op
    // if there are no tokens in the response — it just returns the text unchanged.
    if (DEMASK_RESPONSE_PATTERNS.some((p) => url.includes(p))) {
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("text/event-stream") && response.body) {
        const conversationId = "conv_" + Date.now();
        return new Response(createDemaskingStream(response.body, conversationId), {
          headers: response.headers, status: response.status, statusText: response.statusText,
        });
      }
      if (response.body) {
        const text = await response.text();
        const demaskResult = await sendToBackground("DEMASK", { text, conversationId: "conv_" + Date.now() });
        const restored = demaskResult.success && demaskResult.restoredText ? demaskResult.restoredText : text;
        return new Response(restored, {
          headers: response.headers, status: response.status, statusText: response.statusText,
        });
      }
    }
    return response;
  };

  // ─────────────────────────────────────────────────────────
  // XHR fallback (unchanged from v5)
  // ─────────────────────────────────────────────────────────
  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__pii_url = url || "";
    this.__pii_method = method || "GET";
    return originalXHROpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = async function (body) {
    const url = this.__pii_url || "";
    const isSendEndpoint =
      url.includes(CONVERSATION_SEND_PATTERN) &&
      !url.includes("/conversation/init") &&
      !url.includes("/conversations?") &&
      !url.includes("/f/conversation/prepare");

    if (isSendEndpoint && body != null) {
      try {
        const bodyStr = await readBody(body);
        const originalText = extractMessageText(bodyStr || "");
        if (originalText && originalText.trim()) {
          badgeStats.intercepted++;
          setBadge(`XHR intercepting… (#${badgeStats.intercepted})`, "warn");
          const conversationId = "conv_" + Date.now();
          const maskResult = await sendToBackground("MASK", { text: originalText, conversationId });
          if (!maskResult.success) {
            badgeStats.blocked++;
            badgeStats.lastError = maskResult.error;
            setBadge(`❌ XHR BLOCKED: ${maskResult.error}`, "err");
            this.abort();
            return;
          }
          if (!maskResult.safe_to_send) {
            badgeStats.blocked++;
            setBadge(`❌ XHR BLOCKED: leak`, "err");
            this.abort();
            return;
          }
          const newBodyStr = replaceMessageText(bodyStr, originalText, maskResult.masked_text);
          badgeStats.masked++;
          setBadge(`✓ XHR masked ${maskResult.entities_found} entities`, "ok");
          return originalXHRSend.call(this, newBodyStr);
        }
      } catch (err) {
        dbg("error", "XHR error:", err.message);
        this.abort();
        return;
      }
    }
    return originalXHRSend.call(this, body);
  };

  // ─────────────────────────────────────────────────────────
  // Boot
  // ─────────────────────────────────────────────────────────
  attachBadge();
  if (document.body) observer.observe(document.body, { childList: true });
  else setTimeout(() => { if (document.body) observer.observe(document.body, { childList: true }); }, 100);

  dbg("info", "v6 hooks installed");
  dbg("info", "window.fetch wrapped:", window.fetch !== originalFetch);
  dbg("info", "XMLHttpRequest.send wrapped:", XMLHttpRequest.prototype.send !== originalXHRSend);
  dbg("info", "Backend URL:", BACKEND_URL);
  dbg("info", "Send endpoint pattern:", CONVERSATION_SEND_PATTERN);
  setBadge("v6 loaded · click 'test backend'", "");
})();
