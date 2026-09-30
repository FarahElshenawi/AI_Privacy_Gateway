/**
 * File upload interceptor — runs in MAIN world.
 *
 * Intercepts FormData file uploads to ChatGPT, sends the file through
 * the content-script bridge to the service worker, which sends it to
 * the backend for masking.
 *
 * The masked file is then uploaded instead of the original.
 */

(function () {
  "use strict";

  const MSG_TAG = "__PII_GATEWAY__";
  let messageId = 0;

  const FILE_UPLOAD_PATTERNS = ["/backend-api/files", "/backend-api/upload"];

  function sendToBackground(type, payload) {
    return new Promise((resolve) => {
      const id = ++messageId;
      function handler(event) {
        if (event.source !== window) return;
        const data = event.data;
        if (!data || data.tag !== MSG_TAG || data.id !== id) return;
        window.removeEventListener("message", handler);
        resolve(data.response || { success: false });
      }
      window.addEventListener("message", handler);
      window.postMessage({ tag: MSG_TAG, id, type, payload }, "*");
      setTimeout(() => {
        window.removeEventListener("message", handler);
        resolve({ success: false, error: "Timeout" });
      }, 60000); // 60s for file processing
    });
  }

  const originalFetch = window.fetch;
  // Flag to prevent double-interception (fetch-override.js also intercepts)
  let _intercepting = false;

  // We hook into XMLHttpRequest as well, since ChatGPT may use it for uploads
  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;

  // Intercept fetch for file uploads
  const originalFetchPatched = window.fetch;
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input?.url || "";

    if (
      !_intercepting &&
      FILE_UPLOAD_PATTERNS.some((p) => url.includes(p)) &&
      init &&
      init.body instanceof FormData
    ) {
      _intercepting = true;
      try {
        const formData = init.body;
        const file = formData.get("file");

        if (file && file instanceof File) {
          console.log("[PII Gateway] Intercepted file upload:", file.name);

          const result = await sendToBackground("PROCESS_FILE", {
            file: file,
            filename: file.name,
            contentType: file.type,
          });

          if (!result.success) {
            console.error("[PII Gateway] File masking failed:", result.error);
            // FAIL CLOSED
            return new Response(
              JSON.stringify({ error: "PII Gateway: file masking failed — blocked" }),
              { status: 503, headers: { "Content-Type": "application/json" } }
            );
          }

          // Replace file in FormData with masked version
          if (result.maskedFileBlob) {
            const newFormData = new FormData();
            for (const [key, value] of formData.entries()) {
              if (key === "file") {
                newFormData.append("file", result.maskedFileBlob, file.name);
              } else {
                newFormData.append(key, value);
              }
            }
            init.body = newFormData;
            console.log("[PII Gateway] File masked, uploading masked version");
          }
        }
      } catch (err) {
        console.error("[PII Gateway] File upload interception error:", err);
        return new Response(
          JSON.stringify({ error: "PII Gateway: upload blocked (fail-closed)" }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        );
      } finally {
        _intercepting = false;
      }
    }

    return originalFetchPatched.call(this, input, init);
  };

  console.log("[PII Gateway] File upload interceptor loaded");
})();
