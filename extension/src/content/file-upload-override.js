/**
 * File upload interceptor — runs in MAIN world.
 *
 * Intercepts FormData file uploads to ChatGPT, sends the file through
 * the content-script bridge to the service worker, which sends it to
 * the backend for masking. The masked file is then uploaded instead of
 * the original.
 *
 * This is the SOLE owner of file-upload interception. It registers into
 * the shared middleware registry defined in fetch-override.js rather
 * than touching window.fetch directly — see the long comment at the top
 * of fetch-override.js for why two scripts independently reassigning
 * window.fetch was broken (load order between dynamically-injected
 * <script> tags is not guaranteed) and why this registry fixes it
 * regardless of which of the two scripts happens to execute first.
 */

(function () {
  "use strict";

  const MSG_TAG = "__PII_GATEWAY__";
  let messageId = 0;

  const FILE_UPLOAD_PATTERNS = ["/backend-api/files", "/backend-api/upload"];

  function sendToBackground(type, payload) {
    return new Promise((resolve) => {
      const id = ++messageId;
      let timeoutId;
      function handler(event) {
        if (event.source !== window) return;
        const data = event.data;
        if (!data || data.tag !== MSG_TAG || data.id !== id) return;
        window.removeEventListener("message", handler);
        clearTimeout(timeoutId);
        resolve(data.response || { success: false });
      }
      window.addEventListener("message", handler);
      window.postMessage({ tag: MSG_TAG, id, type, payload }, "*");
      timeoutId = setTimeout(() => {
        window.removeEventListener("message", handler);
        resolve({ success: false, error: "Timeout" });
      }, 60000); // 60s for file processing
    });
  }

  function logEvent(event, details) {
    sendToBackground("LOG_EVENT", { event, details }).catch(() => {});
  }

  function bumpStat(key, amount = 1) {
    sendToBackground("INCREMENT_STAT", { key, amount }).catch(() => {});
  }

  // chrome.runtime.sendMessage (and the window.postMessage hop that feeds
  // it from this MAIN-world script) only JSON-serializes its payload — a
  // File/Blob passed directly comes out the other end as `{}`. Every file
  // (PDF, docx, xlsx, csv, txt, ...) has to cross that boundary as a plain
  // string, so it's base64-encoded here and decoded back into a Blob by
  // the service worker. Chunked to stay well under
  // String.fromCharCode's per-call argument limit.
  const BASE64_CHUNK_SIZE = 0x8000;
  // Fail-closed cap: a very large file would produce a base64 string far
  // past chrome.runtime.sendMessage's payload limit, and would silently
  // hang rather than mask anything — block it explicitly instead.
  const MAX_FILE_BYTES = 20 * 1024 * 1024; // 20 MB

  async function fileToBase64(file) {
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i += BASE64_CHUNK_SIZE) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + BASE64_CHUNK_SIZE));
    }
    return btoa(binary);
  }

  async function handleFileUpload(next, thisArg, input, init, url) {
    const formData = init.body;
    const file = formData.get("file");

    if (!(file instanceof File)) {
      return next(thisArg, input, init);
    }

    // Escape hatch — same flag the conversation-text path checks. A file
    // upload while protection is off is just as reportable as a text one.
    const protectionState = await sendToBackground("GET_PROTECTION_ENABLED", {});
    if (protectionState.success && protectionState.enabled === false) {
      console.warn("[PII Gateway] Protection is OFF — uploading file UNMASKED (escape hatch).");
      logEvent("ESCAPE_HATCH_BYPASS_FILE", { url, filename: file.name });
      bumpStat("bypassCount");
      return next(thisArg, input, init);
    }

    if (file.size > MAX_FILE_BYTES) {
      console.error("[PII Gateway] File too large to mask safely:", file.name, file.size);
      logEvent("BLOCKED_FILE_TOO_LARGE", { url, filename: file.name, size: file.size });
      bumpStat("blocksCount");
      return new Response(
        JSON.stringify({ error: "PII Gateway: file too large to mask — blocked (fail-closed)" }),
        { status: 413, headers: { "Content-Type": "application/json" } }
      );
    }

    try {
      console.log("[PII Gateway] Intercepted file upload:", file.name);

      const dataBase64 = await fileToBase64(file);
      const result = await sendToBackground("PROCESS_FILE", {
        dataBase64,
        filename: file.name,
        contentType: file.type,
      });

      if (!result.success) {
        console.error("[PII Gateway] File masking failed:", result.error);
        logEvent("BLOCKED_FILE_MASK_FAILURE", { url, filename: file.name, error: result.error });
        bumpStat("blocksCount");
        return new Response(
          JSON.stringify({ error: "PII Gateway: file masking failed — blocked (fail-closed)" }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        );
      }

      if (result.maskedFileBase64) {
        // Decode the masked file back into a real Blob here, in the
        // MAIN world, where it can go straight into the FormData that
        // actually gets uploaded — the service worker only ever hands
        // back a base64 string (see comment near fileToBase64 above).
        const binary = atob(result.maskedFileBase64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        const maskedBlob = new Blob([bytes], { type: result.maskedContentType || file.type });

        const newFormData = new FormData();
        for (const [key, value] of formData.entries()) {
          if (key === "file") {
            newFormData.append("file", maskedBlob, file.name);
          } else {
            newFormData.append(key, value);
          }
        }
        init.body = newFormData;
        console.log("[PII Gateway] File masked, uploading masked version");
        bumpStat("filesMasked");
      }

      return next(thisArg, input, init);
    } catch (err) {
      console.error("[PII Gateway] File upload interception error:", err);
      logEvent("BLOCKED_FILE_EXCEPTION", { url, filename: file?.name, error: err.message });
      bumpStat("blocksCount");
      return new Response(
        JSON.stringify({ error: "PII Gateway: upload blocked (fail-closed)" }),
        { status: 503, headers: { "Content-Type": "application/json" } }
      );
    }
  }

  // --- Register into the shared pipeline (see fetch-override.js) ---
  // If fetch-override.js hasn't run yet, __piiGatewayRegister/Rebuild
  // won't exist — fall back to capturing native fetch and registering
  // the globals ourselves so either load order works.
  if (!window.__piiGatewayNativeFetch) {
    window.__piiGatewayNativeFetch = window.fetch;
  }
  window.__piiGatewayWrappers = window.__piiGatewayWrappers || [];
  if (!window.__piiGatewayRebuild) {
    window.__piiGatewayRebuild = function () {
      const native = window.__piiGatewayNativeFetch;
      let chain = (thisArg, input, init) => native.call(thisArg, input, init);
      for (const { middleware } of window.__piiGatewayWrappers) chain = middleware(chain);
      const composed = function (input, init) {
        return chain(this, input, init);
      };
      composed.__piiGatewayInstalled = true;
      window.fetch = composed;
    };
  }
  if (!window.__piiGatewayRegister) {
    window.__piiGatewayRegister = function (name, middleware) {
      if (window.__piiGatewayWrappers.some((w) => w.name === name)) return;
      window.__piiGatewayWrappers.push({ name, middleware });
      window.__piiGatewayRebuild();
    };
  }

  window.__piiGatewayRegister("file-upload-mask", (next) => {
    return function (thisArg, input, init) {
      const url = typeof input === "string" ? input : input?.url || "";
      if (
        FILE_UPLOAD_PATTERNS.some((p) => url.includes(p)) &&
        init &&
        init.body instanceof FormData
      ) {
        return handleFileUpload(next, thisArg, input, init, url);
      }
      return next(thisArg, input, init);
    };
  });

  console.log("[PII Gateway] File upload interceptor loaded");
})();
