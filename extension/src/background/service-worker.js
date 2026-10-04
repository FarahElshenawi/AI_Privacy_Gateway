/**
 * Service worker — manages the token, makes all backend calls.
 *
 * The service worker is the ONLY part of the extension that talks to
 * the local backend. It holds the per-install token and never exposes
 * it to the page context.
 */

const BACKEND_URL = "http://127.0.0.1:8765";
const TOKEN_KEY = "pii_gateway_token";
const PROTECTION_KEY = "protectionEnabled";
const AUDIT_LOG_KEY = "pii_gateway_audit_log";
const AUDIT_LOG_MAX_ENTRIES = 500;

// --- Protection state (the "escape hatch") ---
//
// "Protection Active" in the popup maps to this flag. When it's OFF, the
// MAIN-world override (fetch-override.js) skips masking entirely and sends
// the user's real text straight through. That bypass is powerful enough
// that the spec requires it to be logged every time it's used — see
// logAuditEvent() below and the "ESCAPE_HATCH_BYPASS" event fetch-override.js
// sends on every request it let through unmasked.

async function isProtectionEnabled() {
  const result = await chrome.storage.local.get(PROTECTION_KEY);
  // Default ON — fail-closed-by-default, not fail-open-by-default.
  return result[PROTECTION_KEY] !== false;
}

// --- Audit log ---
//
// Append-only (capped) log of every fail-closed block and every escape-hatch
// bypass, independent of which side (popup or MAIN-world page script)
// triggered it. This is what makes the escape hatch a *logged* escape hatch
// rather than a silent toggle: flipping it, and every request that goes out
// unmasked because of it, is recorded here with a timestamp.

async function logAuditEvent(event, details = {}) {
  const result = await chrome.storage.local.get(AUDIT_LOG_KEY);
  const log = result[AUDIT_LOG_KEY] || [];
  log.push({ ts: Date.now(), event, details });
  while (log.length > AUDIT_LOG_MAX_ENTRIES) log.shift();
  await chrome.storage.local.set({ [AUDIT_LOG_KEY]: log });
}

async function getAuditLog() {
  const result = await chrome.storage.local.get(AUDIT_LOG_KEY);
  return result[AUDIT_LOG_KEY] || [];
}

// --- Stats counters (surfaced in the popup) ---

async function incrementStat(key, amount = 1) {
  if (!amount) return;
  const result = await chrome.storage.local.get(key);
  await chrome.storage.local.set({ [key]: (result[key] || 0) + amount });
}

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
  const data = await resp.json();
  // IMPORTANT: the backend (local-backend/app/api/mask.py) returns snake_case
  // field names (FastAPI/Pydantic default). Every caller of maskText() in this
  // extension expects camelCase (maskedText, safeToSend, entitiesFound) — the
  // same convention demaskText() below already follows for restoredText.
  // A previous version of this function did `{ success: true, ...data }`,
  // which silently left safeToSend/maskedText/entitiesFound undefined and
  // caused fetch-override.js to treat EVERY prompt as an unsafe leak and
  // block it. Do not revert to the spread form.
  return {
    success: true,
    maskedText: data.masked_text,
    pairs: data.pairs,
    entitiesFound: data.entities_found,
    leaks: data.leaks,
    safeToSend: data.safe_to_send,
  };
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

// chrome.runtime.sendMessage only JSON-serializes its payload, so a File
// or Blob can never cross it directly — it silently turns into `{}` on
// the other end. Any file (pdf, docx, xlsx, csv, txt, ...) going IN comes
// from file-upload-override.js as a base64 string (dataBase64); any file
// coming back OUT (the masked file) must leave this function the same
// way, as maskedFileBase64, for file-upload-override.js to decode back
// into a real Blob in the MAIN world before it's actually uploaded.
// btoa/atob are available here — they're part of WindowOrWorkerGlobalScope,
// so they work in a service worker the same as in a page.
function base64ToBlob(base64, contentType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: contentType || "application/octet-stream" });
}

async function blobToBase64(blob) {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

async function processFile(dataBase64, filename, contentType) {
  const token = await getToken();
  const fileBlob = base64ToBlob(dataBase64, contentType);
  const formData = new FormData();
  formData.append("file", fileBlob, filename);
  const resp = await fetch(`${BACKEND_URL}/api/process_file`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!resp.ok) return { success: false, error: `Backend ${resp.status}` };
  const maskedBlob = await resp.blob();
  return {
    success: true,
    maskedFileBase64: await blobToBase64(maskedBlob),
    maskedContentType: maskedBlob.type || contentType,
  };
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
          if (!message.payload?.dataBase64) {
            sendResponse({ success: false, error: "No file provided" });
            return;
          }
          sendResponse(
            await processFile(
              message.payload.dataBase64,
              message.payload.filename,
              message.payload.contentType
            )
          );
          break;
        }
        case "HEALTH":
          sendResponse(await checkHealth());
          break;
        case "GET_PROTECTION_ENABLED": {
          sendResponse({ success: true, enabled: await isProtectionEnabled() });
          break;
        }
        case "LOG_EVENT": {
          // Any part of the extension (MAIN-world override, popup, this
          // file) can call this to append to the shared audit log.
          await logAuditEvent(message.payload?.event || "unknown", message.payload?.details || {});
          sendResponse({ success: true });
          break;
        }
        case "GET_AUDIT_LOG": {
          sendResponse({ success: true, log: await getAuditLog() });
          break;
        }
        case "INCREMENT_STAT": {
          await incrementStat(message.payload?.key, message.payload?.amount ?? 1);
          sendResponse({ success: true });
          break;
        }
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
