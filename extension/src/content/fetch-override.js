/**
 * MAIN-world fetch() override.
 *
 * Runs inside the untrusted page context. Rules:
 *   1. Never store real PII or Vault data here. Forward-only.
 *   2. Every outbound request body is sent to the Local Backend for masking
 *      before it leaves the browser.
 *   3. On Local Backend failure: FAIL CLOSED. Block the request.
 */

(function () {
  "use strict";

  const MSG_TAG = "__PII_GATEWAY__";
  let messageId = 0;

  // Conversation endpoints (ChatGPT sends prompts here)
  const CONVERSATION_PATTERNS = [
    "/backend-api/conversation",
    "/backend-api/f/conversation",
  ];

  // File upload endpoints
  const FILE_UPLOAD_PATTERNS = ["/backend-api/files", "/backend-api/upload"];

  // --- Helper: send message to service worker via content script ---

  function sendToBackground(type, payload) {
    return new Promise((resolve, reject) => {
      const id = ++messageId;

      function handler(event) {
        if (event.source !== window) return;
        const data = event.data;
        if (!data || data.tag !== MSG_TAG || data.id !== id) return;
        window.removeEventListener("message", handler);
        if (data.response && data.response.success) {
          resolve(data.response);
        } else {
          resolve(data.response || { success: false, error: "No response" });
        }
      }

      window.addEventListener("message", handler);
      window.postMessage({ tag: MSG_TAG, id, type, payload }, "*");

      // Timeout after 30 seconds — fail closed
      setTimeout(() => {
        window.removeEventListener("message", handler);
        resolve({ success: false, error: "Backend timeout (30s)" });
      }, 30000);
    });
  }

  // --- Helper: check if URL matches any pattern ---

  function matchesPattern(url, patterns) {
    return patterns.some((p) => url.includes(p));
  }

  // --- Helper: extract user message text from ChatGPT request body ---

  function extractMessageText(body) {
    try {
      const parsed = JSON.parse(body);
      // ChatGPT's request format: { messages: [{ content: { parts: ["text"] } }] }
      if (parsed.messages && Array.isArray(parsed.messages)) {
        const texts = [];
        for (const msg of parsed.messages) {
          // User messages have role "user"
          if (msg.role === "user" && msg.content && msg.content.parts) {
            for (const part of msg.content.parts) {
              if (typeof part === "string") {
                texts.push(part);
              }
            }
          }
        }
        return texts.join("\n");
      }
      return null;
    } catch {
      return null;
    }
  }

  // --- Helper: replace message text in request body ---

  function replaceMessageText(body, originalText, maskedText) {
    try {
      const parsed = JSON.parse(body);
      if (parsed.messages && Array.isArray(parsed.messages)) {
        for (const msg of parsed.messages) {
          if (msg.role === "user" && msg.content && msg.content.parts) {
            for (let i = 0; i < msg.content.parts.length; i++) {
              if (typeof msg.content.parts[i] === "string") {
                msg.content.parts[i] = msg.content.parts[i].replace(
                  originalText,
                  maskedText
                );
              }
            }
          }
        }
      }
      return JSON.stringify(parsed);
    } catch {
      return body;
    }
  }

  // --- Helper: demask SSE response stream ---

  function createDemaskingStream(responseBody, conversationId) {
    const reader = responseBody.getReader();
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let buffer = "";
    let demaskBuffer = "";
    let demaskThreshold = 100; // characters to buffer before demasking

    return new ReadableStream({
      async pull(controller) {
        try {
          const { done, value } = await reader.read();

          if (done) {
            // Demask any remaining buffered text
            if (demaskBuffer) {
              const result = await sendToBackground("DEMASK", {
                text: demaskBuffer,
                conversationId: conversationId,
              });
              if (result.success && result.restoredText) {
                controller.enqueue(encoder.encode(result.restoredText));
              } else {
                controller.enqueue(encoder.encode(demaskBuffer));
              }
            }
            controller.close();
            return;
          }

          buffer += decoder.decode(value, { stream: true });

          // Process complete lines
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const data = line.slice(6).trim();

              if (data === "[DONE]") {
                controller.enqueue(encoder.encode("data: [DONE]\n"));
                continue;
              }

              try {
                const json = JSON.parse(data);
                // Extract text content from the SSE event
                let text = "";
                if (json.v && json.v.message && json.v.message.content) {
                  text = json.v.message.content.parts?.[0] || "";
                } else if (json.message && json.message.content) {
                  text = json.message.content.parts?.[0] || "";
                }

                if (text) {
                  demaskBuffer += text;

                  // If we have enough buffered text, send for demasking
                  if (demaskBuffer.length >= demaskThreshold) {
                    const result = await sendToBackground("DEMASK", {
                      text: demaskBuffer,
                      conversationId: conversationId,
                    });

                    if (result.success && result.restoredText) {
                      // Update the JSON with restored text
                      if (json.v) {
                        if (json.v.message && json.v.message.content && json.v.message.content.parts) {
                          json.v.message.content.parts[0] = result.restoredText;
                        }
                      } else if (json.message && json.message.content) {
                        json.message.content.parts[0] = result.restoredText;
                      }
                    }
                    demaskBuffer = "";
                  }

                  controller.enqueue(
                    encoder.encode(`data: ${JSON.stringify(json)}\n`)
                  );
                } else {
                  // No text content — pass through
                  controller.enqueue(encoder.encode(`data: ${data}\n`));
                }
              } catch {
                // Not JSON — pass through
                controller.enqueue(encoder.encode(`data: ${data}\n`));
              }
            } else {
              controller.enqueue(encoder.encode(line + "\n"));
            }
          }
        } catch (err) {
          controller.error(err);
        }
      },

      cancel() {
        reader.cancel();
      },
    });
  }

  // --- Main fetch override ---

  const originalFetch = window.fetch;

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input?.url || "";

    // --- Handle conversation requests (text prompts) ---
    if (matchesPattern(url, CONVERSATION_PATTERNS) && init && init.body) {
      try {
        // Extract the conversation ID from headers or generate one
        const conversationId = "conv_" + Date.now();
        const originalText = extractMessageText(init.body);

        if (originalText && originalText.trim()) {
          console.log("[PII Gateway] Intercepted prompt, masking...");

          // Send to backend for masking
          const maskResult = await sendToBackground("MASK", {
            text: originalText,
            conversationId: conversationId,
          });

          if (!maskResult.success) {
            // FAIL CLOSED — block the request
            console.error("[PII Gateway] Masking failed:", maskResult.error);
            return new Response(
              JSON.stringify({
                error: "PII Gateway: masking failed — request blocked (fail-closed)",
                detail: maskResult.error,
              }),
              {
                status: 503,
                headers: { "Content-Type": "application/json" },
              }
            );
          }

          if (!maskResult.safeToSend) {
            // FAIL CLOSED — residual scanner found leaks
            console.error("[PII Gateway] Residual scanner found leaks — blocking");
            return new Response(
              JSON.stringify({
                error: "PII Gateway: residual scanner detected leaks — request blocked",
                detail: maskResult.leaks,
              }),
              {
                status: 422,
                headers: { "Content-Type": "application/json" },
              }
            );
          }

          // Replace the prompt text with masked version
          init.body = replaceMessageText(
            init.body,
            originalText,
            maskResult.maskedText
          );

          console.log(
            `[PII Gateway] Prompt masked (${maskResult.entitiesFound} entities). Forwarding.`
          );

          // Make the original request with masked body
          const response = await originalFetch.call(this, input, init);

          // If it's a streaming response (SSE), demask the stream
          const contentType = response.headers.get("content-type") || "";
          if (contentType.includes("text/event-stream") && response.body) {
            const demaskedStream = createDemaskingStream(
              response.body,
              conversationId
            );
            return new Response(demaskedStream, {
              headers: response.headers,
              status: response.status,
              statusText: response.statusText,
            });
          }

          // Non-streaming response — demask the full body
          if (response.body) {
            const text = await response.text();
            const demaskResult = await sendToBackground("DEMASK", {
              text: text,
              conversationId: conversationId,
            });
            const restoredText =
              demaskResult.success && demaskResult.restoredText
                ? demaskResult.restoredText
                : text;
            return new Response(restoredText, {
              headers: response.headers,
              status: response.status,
              statusText: response.statusText,
            });
          }

          return response;
        }
      } catch (err) {
        console.error("[PII Gateway] Interception error:", err);
        // FAIL CLOSED
        return new Response(
          JSON.stringify({
            error: "PII Gateway: interception error — request blocked",
            detail: err.message,
          }),
          {
            status: 503,
            headers: { "Content-Type": "application/json" },
          }
        );
      }
    }

    // --- Handle file uploads ---
    if (matchesPattern(url, FILE_UPLOAD_PATTERNS) && init && init.body instanceof FormData) {
      try {
        const formData = init.body;
        const file = formData.get("file");

        if (file && file instanceof File) {
          console.log("[PII Gateway] Intercepted file upload:", file.name);

          // Send file to backend for processing
          const fileResult = await sendToBackground("PROCESS_FILE", {
            file: file,
            filename: file.name,
            contentType: file.type,
          });

          if (!fileResult.success) {
            console.error("[PII Gateway] File processing failed:", fileResult.error);
            // FAIL CLOSED
            return new Response(
              JSON.stringify({
                error: "PII Gateway: file masking failed — upload blocked",
                detail: fileResult.error,
              }),
              {
                status: 503,
                headers: { "Content-Type": "application/json" },
              }
            );
          }

          // Replace the file in FormData with masked version
          // The masked file blob comes back from the backend
          if (fileResult.maskedFileBlob) {
            // We need to reconstruct the FormData with the masked file
            const newFormData = new FormData();
            for (const [key, value] of formData.entries()) {
              if (key === "file") {
                newFormData.append(
                  "file",
                  fileResult.maskedFileBlob,
                  file.name
                );
              } else {
                newFormData.append(key, value);
              }
            }
            init.body = newFormData;
            console.log("[PII Gateway] File masked, uploading masked version");
          }
        }
      } catch (err) {
        console.error("[PII Gateway] File interception error:", err);
        // FAIL CLOSED
        return new Response(
          JSON.stringify({
            error: "PII Gateway: file interception error — upload blocked",
            detail: err.message,
          }),
          {
            status: 503,
            headers: { "Content-Type": "application/json" },
          }
        );
      }
    }

    // --- Normal request — pass through ---
    return originalFetch.call(this, input, init);
  };

  console.log("[PII Gateway] Fetch interceptor loaded (MAIN world)");
})();
