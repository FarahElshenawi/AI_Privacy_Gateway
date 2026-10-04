/**
 * Doppel — AI Privacy Gateway
 * Service Worker v2.1 — chrome.debugger + CDP Fetch domain
 *
 * Intercepts ChatGPT requests at the network layer (below the page, its
 * service workers and workers) and rewrites them with PII masked by the
 * local backend.
 *
 * DESIGN RULES
 * ============
 * 1. FAIL CLOSED. Any request we classify as sensitive (message send, file
 *    bytes, file reservation) is blocked unless we positively obtained the
 *    body, masked it, and verified the result. Retrieval failure, backend
 *    failure, timeout, residual leak, unexpected exception → failRequest.
 * 2. NEVER LOG USER CONTENT. Logs contain sizes, counts and entity TYPES only —
 *    never message text, filenames, real values or pairs.
 * 3. TYPES COME FROM THE BACKEND (`entity_types`), never from `pairs`
 *    (which hold the real values).
 *
 * Responses are NOT demasked: the user sees surrogate values / [[REDACTED]]
 * in ChatGPT's answers.
 */

import {
  bytesToBase64, base64ToBytes, concatBytes,
  collectUserTextSlots, collectAttachmentNameSlots,
  countResidualOriginals, sniffExtension, splitFilename,
} from "./body.js";

const BACKEND_URL = "http://127.0.0.1:8765";
const TOKEN_KEY = "pii_gateway_token";
const PROTECTION_KEY = "protectionEnabled";
const MAX_ACTIVITY = 20;
const MASK_TIMEOUT_MS = 15000;
const FILE_TIMEOUT_MS = 90000;

const FETCH_PATTERNS = [
  "*://chatgpt.com/backend-api/*",
  "*://chat.openai.com/backend-api/*",
  "*://*.oaiusercontent.com/*",
  "*://*.blob.core.windows.net/*",
].map((urlPattern) => ({ urlPattern, requestStage: "Request" }));

const SESSION_KEY_VAULT_IDS = "vaultIdByConv";

// tabId → true once Fetch is enabled
const attachedTabs = new Set();
const attaching = new Map();            // tabId → in-flight attach promise
// tabId → FIFO of (masked) filenames seen in upload reservations
const reservedNames = new Map();
// tabId → vault id used while the chat has no server-side conversation id yet
const pendingVaultId = new Map();

const isChatGPTUrl = (u) => !!u && (/^https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(u));

// ─────────────────────────────────────────────────────────
// Backend access
// ─────────────────────────────────────────────────────────

async function fetchWithTimeout(url, opts, ms) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

async function getToken() {
  const result = await chrome.storage.local.get(TOKEN_KEY);
  if (result[TOKEN_KEY]) return result[TOKEN_KEY];
  try {
    const resp = await fetchWithTimeout(`${BACKEND_URL}/token`, {}, 5000);
    if (!resp.ok) return null;
    const data = await resp.json();
    if (data.token) {
      await chrome.storage.local.set({ [TOKEN_KEY]: data.token });
      return data.token;
    }
  } catch {
    console.error("[Doppel] Failed to fetch backend token");
  }
  return null;
}

async function maskViaBackend(text, conversationId) {
  const token = await getToken();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const resp = await fetchWithTimeout(`${BACKEND_URL}/api/mask`, {
    method: "POST",
    headers,
    body: JSON.stringify({ text, conversation_id: conversationId }),
  }, MASK_TIMEOUT_MS);
  if (!resp.ok) throw new Error(`backend ${resp.status}`);
  return resp.json();
}

async function maskFileViaBackend(fileBytes, filename, contentType, conversationId) {
  const token = await getToken();
  const form = new FormData();
  form.append("file", new Blob([fileBytes], { type: contentType || "application/octet-stream" }), filename);
  form.append("conversation_id", conversationId);
  const resp = await fetchWithTimeout(`${BACKEND_URL}/api/process_file`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  }, FILE_TIMEOUT_MS);
  if (!resp.ok) throw new Error(`backend ${resp.status}`);
  return new Uint8Array(await resp.arrayBuffer());
}

// ─────────────────────────────────────────────────────────
// Activity log (no user content — types/counts only)
// ─────────────────────────────────────────────────────────

let logChain = Promise.resolve(); // serialize read-modify-write on storage
function logActivity(type, details = {}) {
  logChain = logChain.then(() => doLogActivity(type, details)).catch(() => {});
  return logChain;
}

async function doLogActivity(type, details) {
  const r = await chrome.storage.local.get([
    "activityLog", "promptsMasked", "entitiesMasked", "filesMasked", "blocksCount",
  ]);
  const log = r.activityLog || [];
  log.unshift({
    type,
    time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    entityTypes: details.entityTypes || [],
    entityCount: details.entityCount || 0,
    detail: details.detail || "",
  });
  if (log.length > MAX_ACTIVITY) log.length = MAX_ACTIVITY;

  const stats = {
    activityLog: log,
    promptsMasked: r.promptsMasked || 0,
    entitiesMasked: r.entitiesMasked || 0,
    filesMasked: r.filesMasked || 0,
    blocksCount: r.blocksCount || 0,
  };
  if (type === "mask") { stats.promptsMasked++; stats.entitiesMasked += details.entityCount || 0; }
  else if (type === "block") stats.blocksCount++;
  else if (type === "file") { stats.filesMasked++; stats.entitiesMasked += details.entityCount || 0; }

  await chrome.storage.local.set(stats);
  try { await chrome.runtime.sendMessage({ type: "ACTIVITY_UPDATE" }); } catch { /* popup closed */ }
}

// ─────────────────────────────────────────────────────────
// Vault id: stable per ChatGPT conversation
// ─────────────────────────────────────────────────────────

async function resolveVaultId(tabId, serverConvId) {
  const store = (await chrome.storage.session.get(SESSION_KEY_VAULT_IDS))[SESSION_KEY_VAULT_IDS] || {};
  let id;
  if (serverConvId) {
    id = store[serverConvId];
    if (!id) {
      // First message of a new chat was masked under a pending id: keep using it.
      id = pendingVaultId.get(tabId) || `conv_${serverConvId}`;
      pendingVaultId.delete(tabId);
      store[serverConvId] = id;
      await chrome.storage.session.set({ [SESSION_KEY_VAULT_IDS]: store });
    }
  } else {
    id = pendingVaultId.get(tabId);
    if (!id) {
      id = `new_${tabId}_${crypto.randomUUID()}`;
      pendingVaultId.set(tabId, id);
    }
  }
  return id;
}

// ─────────────────────────────────────────────────────────
// CDP helpers
// ─────────────────────────────────────────────────────────

const cont = (source, requestId, extra = {}) =>
  chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId, ...extra });

async function block(source, requestId, reason, entityTypes = [], entityCount = 0) {
  try {
    await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
      requestId, errorReason: "BlockedByClient",
    });
  } catch (e) {
    console.error("[Doppel] failRequest failed:", e.message);
  }
  await logActivity("block", { detail: reason, entityTypes, entityCount });
}

/**
 * Get the request body as bytes. Returns null when the request has no body.
 * THROWS if a body exists but cannot be retrieved (caller must fail closed).
 */
async function getRequestBody(source, params) {
  const { request, networkId } = params;

  if (Array.isArray(request.postDataEntries) && request.postDataEntries.length) {
    // Newer Chrome: base64 chunks, binary-safe.
    return concatBytes(request.postDataEntries.map((e) => base64ToBytes(e.bytes || "")));
  }
  if (typeof request.postData === "string") {
    return new TextEncoder().encode(request.postData);
  }
  if (!request.hasPostData) return null;

  if (!networkId) throw new Error("body not inlined and no networkId");
  const res = await chrome.debugger.sendCommand(source, "Network.getRequestPostData", {
    requestId: networkId,
  });
  if (!res || typeof res.postData !== "string") throw new Error("empty getRequestPostData result");
  return res.base64Encoded ? base64ToBytes(res.postData) : new TextEncoder().encode(res.postData);
}

// ─────────────────────────────────────────────────────────
// Request classification
// ─────────────────────────────────────────────────────────

function classify(request, urlObj) {
  const path = urlObj.pathname.replace(/\/+$/, "");
  const method = request.method;
  // Only the real send endpoints. NOT /conversation/init, /f/conversation/prepare,
  // /conversations, /conversation/{id}/... (no user text, or not a send).
  if (method === "POST" && (path === "/backend-api/conversation" || path === "/backend-api/f/conversation")) {
    return "send";
  }
  if ((method === "PUT" || method === "POST") &&
      (urlObj.hostname.endsWith(".oaiusercontent.com") || urlObj.hostname.endsWith(".blob.core.windows.net"))) {
    return "file-put";
  }
  if (method === "POST" && /^\/backend-api\/files(\/upload_reservations)?$/.test(path)) return "file-reserve";
  return null;
}

// ─────────────────────────────────────────────────────────
// Handlers
// ─────────────────────────────────────────────────────────

async function maskStringSlots(slots, vaultId) {
  const types = new Set();
  let count = 0;
  for (const slot of slots) {
    const original = slot.get();
    if (!original || !original.trim()) continue;
    const r = await maskViaBackend(original, vaultId);
    if (!r.safe_to_send) {
      const e = new Error("residual leak");
      e.leakTypes = (r.leaks || []).map((l) => l.type).filter(Boolean);
      e.leakCount = (r.leaks || []).length;
      throw e;
    }
    // Verify the real values are really gone from what we will send.
    if (countResidualOriginals(r.masked_text, r.pairs) > 0) throw new Error("verification failed");
    slot.set(r.masked_text);
    count += r.entities_found || 0;
    (r.entity_types || []).forEach((t) => types.add(t));
  }
  return { types: [...types], count };
}

async function handleSend(source, params, body) {
  const { requestId, tabId } = { requestId: params.requestId, tabId: source.tabId };
  let parsed;
  try {
    parsed = JSON.parse(new TextDecoder().decode(body));
  } catch {
    return block(source, requestId, "Unparseable send body");
  }

  const textSlots = collectUserTextSlots(parsed);
  const nameSlots = collectAttachmentNameSlots(parsed);
  if (!textSlots.some((s) => s.get() && s.get().trim()) && nameSlots.length === 0) {
    return cont(source, requestId); // nothing user-authored to mask
  }

  const vaultId = await resolveVaultId(tabId, parsed.conversation_id || null);
  let result;
  try {
    result = await maskStringSlots([...textSlots, ...nameSlots], vaultId);
  } catch (err) {
    return block(source, requestId,
      err.leakCount ? "Residual scanner found leaks" : `Masking failed: ${err.message}`,
      err.leakTypes || [], err.leakCount || 0);
  }

  const out = bytesToBase64(new TextEncoder().encode(JSON.stringify(parsed)));
  try {
    await cont(source, requestId, { postData: out });
  } catch (e) {
    console.error("[Doppel] continueRequest (masked) failed:", e.message);
    await block(source, requestId, "continueRequest failed");
    return;
  }
  await logActivity("mask", { entityTypes: result.types, entityCount: result.count });
}

/** Mask the file_name in an upload reservation and remember it for the PUT. */
async function handleReserve(source, params, body) {
  const { requestId } = params;
  const tabId = source.tabId;
  let parsed;
  try { parsed = JSON.parse(new TextDecoder().decode(body)); } catch { parsed = null; }
  if (!parsed || typeof parsed.file_name !== "string") {
    return cont(source, requestId); // other /files call, no filename inside
  }

  const { stem, ext } = splitFilename(parsed.file_name);
  const slot = { get: () => stem, set: (v) => { parsed.file_name = v + ext; } };
  try {
    const vaultId = await resolveVaultId(tabId, null);
    await maskStringSlots([slot], vaultId);
  } catch (err) {
    return block(source, requestId, `Filename masking failed: ${err.message}`);
  }
  const q = reservedNames.get(tabId) || [];
  q.push(parsed.file_name);
  if (q.length > 20) q.shift();
  reservedNames.set(tabId, q);

  await cont(source, requestId, {
    postData: bytesToBase64(new TextEncoder().encode(JSON.stringify(parsed))),
  });
}

async function handleFilePut(source, params, body) {
  const { requestId, request } = params;
  const tabId = source.tabId;
  if (!body || body.length === 0) return cont(source, requestId); // nothing to leak

  const headers = request.headers || {};
  const contentType = headers["Content-Type"] || headers["content-type"] || "application/octet-stream";
  const queue = reservedNames.get(tabId) || [];
  let filename = queue.shift();
  if (!filename) filename = `uploaded_file${sniffExtension(body, contentType)}`;

  const vaultId = await resolveVaultId(tabId, null);
  let masked;
  try {
    masked = await maskFileViaBackend(body, filename, contentType, vaultId);
  } catch (err) {
    return block(source, requestId, `File masking failed: ${err.message}`);
  }
  if (!masked || masked.length === 0) return block(source, requestId, "Empty masked file");

  try {
    await cont(source, requestId, { postData: bytesToBase64(masked) });
  } catch (e) {
    console.error("[Doppel] continueRequest (file) failed:", e.message);
    await block(source, requestId, "continueRequest failed");
    return;
  }
  // The file pipeline does not report entity counts; count the file, not entities.
  await logActivity("file", { detail: "File masked", entityCount: 0 });
}

// ─────────────────────────────────────────────────────────
// CDP event router
// ─────────────────────────────────────────────────────────

chrome.debugger.onEvent.addListener(async (source, method, params) => {
  if (method !== "Fetch.requestPaused") return;
  const { requestId, request } = params;
  if (params.responseStatusCode !== undefined || params.responseErrorReason !== undefined) {
    // We only enable the Request stage; if a response-stage event slips in, pass it on.
    try { await chrome.debugger.sendCommand(source, "Fetch.continueResponse", { requestId }); } catch {}
    return;
  }

  let kind = null;
  try {
    kind = classify(request, new URL(request.url));
  } catch { kind = null; }

  // Content-free diagnostics (host/method/kind only — never paths, queries or bodies).
  try {
    const h = new URL(request.url).hostname;
    if (request.method !== "GET" && request.method !== "OPTIONS") {
      console.debug(`[Doppel] ${request.method} ${h} -> ${kind || "passthrough"} hasBody=${!!request.hasPostData}`);
    }
  } catch {}

  if (!kind) {
    try { await cont(source, requestId); } catch (e) { console.error("[Doppel] passthrough failed:", e.message); }
    return;
  }

  // Protection switched off by the user → pass through (we also detach, this is a backstop)
  const { [PROTECTION_KEY]: enabled } = await chrome.storage.local.get(PROTECTION_KEY);
  if (enabled === false) {
    try { await cont(source, requestId); } catch {}
    return;
  }

  try {
    const body = await getRequestBody(source, params);
    if (kind === "send") {
      if (!body) return cont(source, requestId);
      return await handleSend(source, params, body);
    }
    if (kind === "file-reserve") {
      if (!body) return cont(source, requestId);
      return await handleReserve(source, params, body);
    }
    return await handleFilePut(source, params, body);
  } catch (err) {
    // Anything unexpected on a sensitive request → block, never leak.
    console.error(`[Doppel] ${kind} handler error:`, err.message);
    await block(source, requestId, `Interception error (${kind})`);
  }
});

// ─────────────────────────────────────────────────────────
// Attach / detach (+ recovery after service-worker restarts)
// ─────────────────────────────────────────────────────────

async function enableFetch(tabId) {
  await chrome.debugger.sendCommand({ tabId }, "Network.enable");
  await chrome.debugger.sendCommand({ tabId }, "Fetch.enable", { patterns: FETCH_PATTERNS });
  attachedTabs.add(tabId);
}

function attachDebuggerToTab(tabId) {
  if (attachedTabs.has(tabId)) return Promise.resolve();
  if (attaching.has(tabId)) return attaching.get(tabId);
  const p = (async () => {
    try {
      try {
        await chrome.debugger.attach({ tabId }, "1.3");
      } catch (e) {
        // After an SW restart the session may still be ours — adopt it.
        const targets = await chrome.debugger.getTargets();
        const mine = targets.find((t) => t.tabId === tabId && t.attached && t.extensionId === chrome.runtime.id);
        if (!mine) throw e;
      }
      await enableFetch(tabId);
    } catch (err) {
      console.error(`[Doppel] Attach failed for tab ${tabId}:`, err.message);
    } finally {
      attaching.delete(tabId);
      updateBadge(tabId);
    }
  })();
  attaching.set(tabId, p);
  return p;
}

async function detachDebuggerFromTab(tabId) {
  attachedTabs.delete(tabId);
  reservedNames.delete(tabId);
  pendingVaultId.delete(tabId);
  try { await chrome.debugger.detach({ tabId }); } catch { /* already detached */ }
  updateBadge(tabId);
}

function updateBadge(tabId) {
  chrome.tabs.get(tabId).then((tab) => {
    if (!isChatGPTUrl(tab.url)) return chrome.action.setBadgeText({ tabId, text: "" });
    const on = attachedTabs.has(tabId);
    chrome.action.setBadgeText({ tabId, text: on ? "" : "!" });
    chrome.action.setBadgeBackgroundColor({ tabId, color: "#d93025" });
  }).catch(() => {});
}

async function syncAllTabs() {
  const { [PROTECTION_KEY]: enabled } = await chrome.storage.local.get(PROTECTION_KEY);
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!isChatGPTUrl(tab.url)) continue;
    if (enabled === false) await detachDebuggerFromTab(tab.id);
    else await attachDebuggerToTab(tab.id);
  }
}

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab.url;
  if (!url) return;
  if (!isChatGPTUrl(url)) {
    if (changeInfo.url && attachedTabs.has(tabId)) await detachDebuggerFromTab(tabId);
    return;
  }
  const { [PROTECTION_KEY]: enabled } = await chrome.storage.local.get(PROTECTION_KEY);
  if (enabled === false) return;
  if (changeInfo.status === "loading" || changeInfo.status === "complete") {
    await attachDebuggerToTab(tabId);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => detachDebuggerFromTab(tabId));

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId === undefined) return;
  attachedTabs.delete(source.tabId);
  updateBadge(source.tabId);
  console.warn(`[Doppel] Debugger detached from tab ${source.tabId}: ${reason}`);
});

// Toggle in the popup → attach/detach immediately.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[PROTECTION_KEY]) syncAllTabs();
});

// ─────────────────────────────────────────────────────────
// Popup messages
// ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      switch (message.type) {
        case "PING":
          sendResponse({ success: true, pong: Date.now() });
          break;

        case "HEALTH": {
          try {
            const resp = await fetchWithTimeout(`${BACKEND_URL}/health`, {}, 3000);
            if (resp.ok) sendResponse({ success: true, status: (await resp.json()).status });
            else sendResponse({ success: false, status: `HTTP ${resp.status}` });
          } catch {
            sendResponse({ success: false, status: "offline" });
          }
          break;
        }

        case "GET_DEBUGGER_STATUS": {
          const tabs = [];
          for (const tabId of [...attachedTabs]) {
            try {
              const tab = await chrome.tabs.get(tabId);
              tabs.push({ tabId, url: tab.url, title: tab.title });
            } catch {
              attachedTabs.delete(tabId);
            }
          }
          sendResponse({ success: true, attachedTabs: tabs, count: tabs.length });
          break;
        }

        case "ATTACH_NOW": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab) return sendResponse({ success: false, error: "No active tab" });
          if (!isChatGPTUrl(tab.url)) return sendResponse({ success: false, error: "Active tab is not chatgpt.com" });
          await attachDebuggerToTab(tab.id);
          sendResponse({ success: attachedTabs.has(tab.id), tabId: tab.id,
                         error: attachedTabs.has(tab.id) ? undefined : "Attach failed" });
          break;
        }

        case "DETACH_NOW": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab) return sendResponse({ success: false, error: "No active tab" });
          await detachDebuggerFromTab(tab.id);
          sendResponse({ success: true });
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
  return true;
});

// ─────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────

chrome.runtime.onStartup.addListener(() => syncAllTabs().catch(() => {}));

// MV3 service workers sleep and are not started by an extension reload. A periodic
// alarm wakes us so ChatGPT tabs are never left silently unprotected.
chrome.alarms.create("doppel-sync", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "doppel-sync") syncAllTabs().catch(() => {});
});

chrome.runtime.onInstalled.addListener(async () => {
  syncAllTabs().catch(() => {});
  await getToken();
  const cur = await chrome.storage.local.get([PROTECTION_KEY, "promptsMasked"]);
  // Don't wipe stats/toggle on extension updates.
  if (cur.promptsMasked === undefined) {
    await chrome.storage.local.set({
      promptsMasked: 0, entitiesMasked: 0, filesMasked: 0, blocksCount: 0, activityLog: [],
    });
  }
  if (cur[PROTECTION_KEY] === undefined) await chrome.storage.local.set({ [PROTECTION_KEY]: true });
});

// On every SW start (including restarts), re-adopt / re-attach to ChatGPT tabs.
syncAllTabs().catch((e) => console.error("[Doppel] initial sync failed:", e.message));

console.log("[Doppel] Service worker v2.1 loaded");
