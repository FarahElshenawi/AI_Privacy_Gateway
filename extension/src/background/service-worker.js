/**
 * Service worker — manages the token, makes all backend calls.
 *
 * The service worker is the ONLY part of the extension that talks to
 * the local backend. It holds the per-install token and never exposes
 * it to the page context.
 */

const BACKEND_URL = "http://127.0.0.1:8765";
const TOKEN_KEY = "pii_gateway_token";

// --- Token management ---

async function getToken() {
  const result = await chrome.storage.local.get(TOKEN_KEY);
  if (result[TOKEN_KEY]) {
    return result[TOKEN_KEY];
  }
  try {
    const resp = await fetch(`${BACKEND_URL}/token`);
    if (!resp.ok) return null;
    const data = await resp.json();
    if (data.token) {
      await chrome.storage.local.set({ [TOKEN_KEY]: data.token });
      return data.token;
    }
  } catch (err) {
    console.error("[PII Gateway SW] Failed to fetch token:", err);
  }
  return null;
}

async function makeAuthHeaders() {
  const token = await getToken();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

// --- Backend API calls ---

async function maskText(text, conversationId) {
  const headers = await makeAuthHeaders();
  const resp = await fetch(`${BACKEND_URL}/api/mask`, {
    method: "POST",
    headers,
    body: JSON.stringify({ text, conversation_id: conversationId }),
  });
  if (!resp.ok) return { success: false, error: `Backend ${resp.status}` };
  return { success: true, ...(await resp.json()) };
}

async function demaskText(text, conversationId) {
  const headers = await makeAuthHeaders();
  const resp = await fetch(`${BACKEND_URL}/api/demask`, {
    method: "POST",
    headers,
    body: JSON.stringify({ text, conversation_id: conversationId }),
  });
  if (!resp.ok) return { success: false, error: `Backend ${resp.status}` };
  const data = await resp.json();
  return { success: true, restoredText: data.restored_text };
}

async function processFile(file, filename) {
  const token = await getToken();
  const formData = new FormData();
  formData.append("file", file, filename);
  const resp = await fetch(`${BACKEND_URL}/api/process_file`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!resp.ok) return { success: false, error: `Backend ${resp.status}` };
  return { success: true, maskedFileBlob: await resp.blob() };
}

async function checkHealth() {
  try {
    const resp = await fetch(`${BACKEND_URL}/health`);
    if (resp.ok) {
      const data = await resp.json();
      return { success: true, status: data.status };
    }
    return { success: false, status: "error" };
  } catch {
    return { success: false, status: "offline" };
  }
}

// --- Message handler ---

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      switch (message.type) {
        case "MASK": {
          if (!message.payload?.text) {
            sendResponse({ success: false, error: "No text provided" });
            return;
          }
          sendResponse(await maskText(message.payload.text, message.payload.conversationId || "default"));
          break;
        }
        case "DEMASK": {
          if (!message.payload?.text) {
            sendResponse({ success: false, error: "No text provided" });
            return;
          }
          sendResponse(await demaskText(message.payload.text, message.payload.conversationId || "default"));
          break;
        }
        case "PROCESS_FILE": {
          if (!message.payload?.file) {
            sendResponse({ success: false, error: "No file provided" });
            return;
          }
          sendResponse(await processFile(message.payload.file, message.payload.filename));
          break;
        }
        case "HEALTH":
          sendResponse(await checkHealth());
          break;
        case "GET_TOKEN": {
          if (sender.tab) {
            sendResponse({ success: false, error: "Not available from page context" });
            return;
          }
          const token = await getToken();
          sendResponse({ success: true, token });
          break;
        }
        case "RESET_TOKEN":
          await chrome.storage.local.remove(TOKEN_KEY);
          sendResponse({ success: true });
          break;
        default:
          sendResponse({ success: false, error: `Unknown: ${message.type}` });
      }
    } catch (err) {
      sendResponse({ success: false, error: err.message });
    }
  })();
  return true; // async response
});

chrome.runtime.onInstalled.addListener(async () => {
  console.log("[PII Gateway] Installed — fetching token...");
  await getToken();
});

console.log("[PII Gateway] Service worker loaded");
