import { bytesToBase64 } from "./body.js";
import { createState } from "./state.js";
export const BACKEND_URL = "http://127.0.0.1:8765";
export const TOKEN_KEY = "pii_gateway_token";
export const PROTECTION_KEY = "protectionEnabled";
export const LOCK_KEY = "protectionLocked";    // chrome.storage.managed (enterprise policy): protection cannot be turned off
export const DEMASK_KEY = "demaskEnabled";   // default ON; popup: "Show real values in replies"
export const MAX_ACTIVITY = 20;
export const MASK_TIMEOUT_MS = 15000;
export const FILE_TIMEOUT_MS = 90000;

// Narrow patterns: only pause requests that can carry user content. Every
// paused request costs a round-trip through this worker, so don't pause
// /backend-api/me, /models, /conversations, telemetry, etc.
export const FETCH_PATTERNS = [
  "*://chatgpt.com/backend-api/conversation",
  "*://chatgpt.com/backend-api/f/conversation*",
  "*://chatgpt.com/backend-api/files*",
  "*://chat.openai.com/backend-api/conversation",
  "*://chat.openai.com/backend-api/f/conversation*",
  "*://chat.openai.com/backend-api/files*",
  "*://*.oaiusercontent.com/*",
  "*://*.blob.core.windows.net/*",
  // Gemini
  "*://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate*",
  "*://content-push.googleapis.com/*",
  "*://push.clients6.google.com/*",
].map((urlPattern) => ({ urlPattern, requestStage: "Request" }));

/** True when an administrator has locked protection on (managed policy). Unreadable policy = not locked. */
export async function protectionLocked() {
  try { return (await chrome.storage.managed.get(LOCK_KEY))[LOCK_KEY] === true; } catch { return false; }
}

/** Is protection on? A locked policy wins over the user's own switch. */
export async function protectionOn() {
  if (await protectionLocked()) return true;
  const { [PROTECTION_KEY]: enabled } = await chrome.storage.local.get(PROTECTION_KEY);
  return enabled !== false;
}

export const SESSION_KEY_VAULT_IDS = "vaultIdByConv";

export const attachedTabs = new Set();
export const attaching = new Map();            // tabId → in-flight attach promise
// These four survive the worker being stopped and restarted (see state.js).
export const persisted = createState();
export const reservedNames = persisted.map("reservedNames");              // tabId → FIFO of masked ChatGPT upload filenames
export const authorizedMaskedFiles = persisted.map("authorizedMaskedFiles", {   // tabId → FIFO of { hash, expiresAt, filename }
  strip: (q) => q.map(({ hash, expiresAt, filename }) => ({ hash, expiresAt, filename })),   // never the bytes
});
export const geminiUploads = persisted.map("geminiUploads");              // tabId → FIFO of { name, type } from resumable "start"
export const pendingVaultId = persisted.map("pendingVaultId");            // tabId → vault id while the chat has no server id yet
export const stateReady = persisted.hydrate();

export const isChatGPTUrl = (u) => !!u && (/^https:\/\/(chatgpt\.com|chat\.openai\.com|gemini\.google\.com)\//.test(u));
export const GEMINI_UPLOAD_HOSTS = new Set(["content-push.googleapis.com", "push.clients6.google.com"]);
export const getHeader = (headers, name) => {
  const k = Object.keys(headers || {}).find((h) => h.toLowerCase() === name);
  return k ? headers[k] : undefined;
};
export const encodeJson = (obj) => bytesToBase64(new TextEncoder().encode(JSON.stringify(obj)));


// ─────────────────────────────────────────────────────────
// Diagnostics (content-free)
// ─────────────────────────────────────────────────────────

let traceChain = Promise.resolve();
export function trace(entry) {
  traceChain = traceChain.then(async () => {
    const cur = (await chrome.storage.session.get("trace")).trace || [];
    cur.unshift({ t: new Date().toLocaleTimeString(), ...entry });
    if (cur.length > 25) cur.length = 25;
    await chrome.storage.session.set({ trace: cur });
  }).catch(() => {});
}
export const setDiag = (patch) => chrome.storage.session.get("diag").then((r) =>
  chrome.storage.session.set({ diag: { ...(r.diag || {}), ...patch } })).catch(() => {});
