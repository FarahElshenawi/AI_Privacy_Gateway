/**
 * File upload interception — intercepts file uploads to ChatGPT.
 *
 * When the user uploads a file to chat.openai.com, this script:
 *   1. Intercepts the upload
 *   2. Sends the file to the local backend (localhost:8765/process_file)
 *   3. Receives the masked file back
 *   4. Uploads the MASKED file to ChatGPT instead of the original
 *
 * This runs in the MAIN world so it can override window.fetch.
 */

(function () {
  "use strict";

  const BACKEND_URL = "http://127.0.0.1:8765/api/process_file";
  const TOKEN_URL = "http://127.0.0.1:8765/token";
  const CHATGPT_UPLOAD_URL = "https://chat.openai.com/backend-api/files";

  // Cache the install token
  let installToken = null;

  async function getToken() {
    if (installToken) return installToken;
    try {
      const resp = await fetch(TOKEN_URL);
      const data = await resp.json();
      installToken = data.token;
      return installToken;
    } catch (e) {
      console.error("[PII Gateway] Failed to get token:", e);
      return null;
    }
  }

  // Intercept fetch calls
  const originalFetch = window.fetch;
  window.fetch = async function (url, options) {
    // Check if this is a file upload to ChatGPT
    if (
      typeof url === "string" &&
      url.includes(CHATGPT_UPLOAD_URL) &&
      options &&
      options.body instanceof FormData
    ) {
      try {
        const formData = options.body;
        const file = formData.get("file");

        if (file && file instanceof File) {
          console.log("[PII Gateway] Intercepted file upload:", file.name);

          // Get the auth token
          const token = await getToken();
          if (!token) {
            console.warn("[PII Gateway] No token — uploading original file");
            return originalFetch.call(this, url, options);
          }

          // Send the original file to the backend for processing
          const backendFormData = new FormData();
          backendFormData.append("file", file, file.name);

          const backendResp = await fetch(BACKEND_URL, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${token}`,
            },
            body: backendFormData,
          });

          if (!backendResp.ok) {
            console.error(
              "[PII Gateway] Backend error:",
              backendResp.status,
              await backendResp.text()
            );
            // Fail-closed: don't upload the original if masking failed
            throw new Error(
              `[PII Gateway] Backend processing failed: ${backendResp.status}. ` +
                "File upload blocked (fail-closed)."
            );
          }

          // Get the masked file
          const maskedBlob = await backendResp.blob();
          const maskedFile = new File([maskedBlob], file.name, {
            type: file.type,
          });

          console.log(
            "[PII Gateway] File masked successfully, uploading masked version"
          );

          // Replace the file in the FormData
          formData.set("file", maskedFile, file.name);

          // Continue with the original upload (now with the masked file)
          return originalFetch.call(this, url, options);
        }
      } catch (e) {
        console.error("[PII Gateway] File interception error:", e);
        // Fail-closed: block the upload if something went wrong
        throw e;
      }
    }

    // Not a file upload — pass through to original fetch
    return originalFetch.call(this, url, options);
  };

  console.log("[PII Gateway] File upload interceptor loaded");
})();
