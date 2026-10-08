/**
 * Doppel — AI Privacy Gateway
 * Service Worker v2.3 — chrome.debugger + CDP Fetch domain
 *
 * Intercepts ChatGPT / Gemini requests at the network layer (below the page)
 * and rewrites them with PII masked by the local backend.
 *
 * DESIGN RULES
 * ============
 * 1. FAIL CLOSED. Any request we classify as sensitive (message send, prompt
 *    draft, file bytes, file reservation) is blocked unless we positively
 *    obtained the body, masked it, and verified the result. Retrieval
 *    failure, backend failure, timeout, residual leak, unexpected exception,
 *    unsupported upload shape → failRequest.
 * 2. NEVER LOG USER CONTENT. Logs contain sizes, counts and entity TYPES only.
 * 3. TYPES COME FROM THE BACKEND (`entity_types`), never from `pairs`.
 *
 * Covered: prompt text (send + prepare), attachment names, and file bytes for
 * PDF / Word / Excel / text via ChatGPT (reserve → blob PUT) and Gemini
 * (multipart and resumable "start" → "upload, finalize").
 *
 * Replies are demasked in the page (src/content/demask.js), not on the wire; see handleGetMapping.
 */

import {
  bytesToBase64, base64ToBytes, concatBytes,
  collectUserTextSlots, collectAttachmentNameSlots, collectPrepareSlots,
  countResidualOriginals, fixFilename, splitFilename, convIdFromUrl, describeBackendError,
  parseGeminiBody, collectGeminiSlots, buildGeminiBody, geminiConversationId,
  parseGeminiStartName, buildGeminiStartBody,
  parseMultipart, buildMultipart, boundaryFromContentType,
} from "./body.js";

const BACKEND_URL = "http://127.0.0.1:8765";
const TOKEN_KEY = "pii_gateway_token";
const PROTECTION_KEY = "protectionEnabled";
const DEMASK_KEY = "demaskEnabled";   // default ON; popup: "Show real values in replies"
const MAX_ACTIVITY = 20;
const MASK_TIMEOUT_MS = 15000;
const FILE_TIMEOUT_MS = 90000;

// Narrow patterns: only pause requests that can carry user content. Every
// paused request costs a round-trip through this worker, so don't pause
// /backend-api/me, /models, /conversations, telemetry, etc.
const FETCH_PATTERNS = [
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

const SESSION_KEY_VAULT_IDS = "vaultIdByConv";

const attachedTabs = new Set();
const attaching = new Map();            // tabId → in-flight attach promise
const reservedNames = new Map();        // tabId → FIFO of masked ChatGPT upload filenames
const authorizedMaskedFiles = new Map(); // tabId → FIFO of { hash, expiresAt, filename }
const geminiUploads = new Map();        // tabId → FIFO of { name, type } from resumable "start"
const pendingVaultId = new Map();       // tabId → vault id while the chat has no server id yet

const isChatGPTUrl = (u) => !!u && (/^https:\/\/(chatgpt\.com|chat\.openai\.com|gemini\.google\.com)\//.test(u));
const GEMINI_UPLOAD_HOSTS = new Set(["content-push.googleapis.com", "push.clients6.google.com"]);
const getHeader = (headers, name) => {
  const k = Object.keys(headers || {}).find((h) => h.toLowerCase() === name);
  return k ? headers[k] : undefined;
};
const encodeJson = (obj) => bytesToBase64(new TextEncoder().encode(JSON.stringify(obj)));

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

/** Error whose message is already a short, content-free, user-facing reason. */
class BackendError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

/**
 * POST to the backend with the install token. If the backend rejects the token
 * (401 — e.g. ~/.pii_gateway_token.json was regenerated), drop the cached token,
 * fetch a fresh one and retry ONCE. `makeInit` is a function so the body
 * (FormData) is rebuilt for the retry.
 */
async function backendPost(path, makeInit, timeoutMs) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const token = await getToken();
    const init = makeInit();
    init.method = "POST";
    init.headers = { ...(init.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
    let resp;
    try {
      resp = await fetchWithTimeout(`${BACKEND_URL}${path}`, init, timeoutMs);
    } catch (e) {
      throw new BackendError(e.name === "AbortError" ? "Local backend timed out" : "Local backend unreachable");
    }
    if (resp.status === 401 && attempt === 0) {
      await chrome.storage.local.remove(TOKEN_KEY);
      continue;
    }
    return resp;
  }
  throw new BackendError("Backend rejected the token", 401);
}

async function maskViaBackend(text, conversationId) {
  const resp = await backendPost("/api/mask", () => ({
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, conversation_id: conversationId }),
  }), MASK_TIMEOUT_MS);
  if (!resp.ok) throw new BackendError(describeBackendError(resp.status, null, "text"), resp.status);
  return resp.json();
}

/**
 * Mask a file via /api/process_file. Returns the masked bytes plus the coverage
 * headers the backend sets (X-DLP-*). On failure the backend answers 4xx/5xx
 * with a JSON `detail` of machine codes (blockers / leak types) — surfaced as
 * the block reason so the user can see WHY a file was refused.
 */
async function maskFileViaBackend(fileBytes, filename, contentType, conversationId) {
  const resp = await backendPost("/api/process_file", () => {
    const form = new FormData();
    form.append("file", new Blob([fileBytes], { type: contentType || "application/octet-stream" }), filename);
    form.append("conversation_id", conversationId);
    return { body: form };
  }, FILE_TIMEOUT_MS);
  if (!resp.ok) {
    let detail = null;
    try { detail = (await resp.json()).detail; } catch { /* not JSON */ }
    throw new BackendError(describeBackendError(resp.status, detail, "file"), resp.status);
  }
  const csv = (h) => (resp.headers.get(h) || "").split(",").map((s) => s.trim()).filter(Boolean);
  return {
    bytes: new Uint8Array(await resp.arrayBuffer()),
    degraded: resp.headers.get("X-DLP-Degraded") === "true",
    uncovered: csv("X-DLP-Uncovered-Labels"),
    warnings: csv("X-DLP-Warnings"),
    replacements: parseInt(resp.headers.get("X-DLP-Replacements") || "0", 10) || 0,
  };
}

const coverageNote = (labels) =>
  labels && labels.length ? `partial coverage — not checked: ${labels.join(", ")}` : "partial coverage";

// ─────────────────────────────────────────────────────────
// Activity log (no user content — types/counts only)
// ─────────────────────────────────────────────────────────

let logChain = Promise.resolve();
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
// Vault id: stable per conversation (shared by prompts AND files)
// ─────────────────────────────────────────────────────────

async function tabConvId(tabId) {
  try { return convIdFromUrl((await chrome.tabs.get(tabId)).url); } catch { return null; }
}

async function resolveVaultId(tabId, serverConvId) {
  // File uploads carry no conversation id in their bodies; the tab URL does
  // (/c/<id>, /app/<id>). Without this, a file uploaded into an existing chat
  // got a different vault than the prompts → inconsistent surrogates.
  serverConvId = serverConvId || await tabConvId(tabId);
  const store = (await chrome.storage.session.get(SESSION_KEY_VAULT_IDS))[SESSION_KEY_VAULT_IDS] || {};
  let id;
  if (serverConvId) {
    id = store[serverConvId];
    if (!id) {
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
// Diagnostics (content-free)
// ─────────────────────────────────────────────────────────

let traceChain = Promise.resolve();
function trace(entry) {
  traceChain = traceChain.then(async () => {
    const cur = (await chrome.storage.session.get("trace")).trace || [];
    cur.unshift({ t: new Date().toLocaleTimeString(), ...entry });
    if (cur.length > 25) cur.length = 25;
    await chrome.storage.session.set({ trace: cur });
  }).catch(() => {});
}
const setDiag = (patch) => chrome.storage.session.get("diag").then((r) =>
  chrome.storage.session.set({ diag: { ...(r.diag || {}), ...patch } })).catch(() => {});

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
  trace({ outcome: "BLOCKED", detail: reason });
  await logActivity("block", { detail: reason, entityTypes, entityCount });
}

/**
 * Get the request body as bytes. Returns null when the request has no body.
 * THROWS if a body exists but cannot be retrieved faithfully (caller fails closed).
 *
 * Why this is stricter than before:
 *  - A File/Blob-backed body (what the browser uses for uploads) shows up in
 *    `postDataEntries` WITHOUT `bytes`. Treating that as "empty" let the whole
 *    file go out unmasked (fail-open). Such entries now force getRequestPostData.
 *  - `request.postData` is a UTF-8 *string*; for binary (PDF/docx/xlsx) it is
 *    lossy (U+FFFD). Lossy strings are never trusted.
 */
async function getRequestBody(source, params) {
  const { request, networkId } = params;
  const entries = request.postDataEntries;

  if (Array.isArray(entries) && entries.length) {
    if (entries.every((e) => typeof e.bytes === "string")) {
      return concatBytes(entries.map((e) => base64ToBytes(e.bytes)));
    }
    // blob/file-backed entry without inline bytes → fall through to getRequestPostData
  } else if (typeof request.postData === "string" && !request.postData.includes("�")) {
    return new TextEncoder().encode(request.postData);
  } else if (!request.hasPostData && typeof request.postData !== "string") {
    return null;
  }

  if (!networkId) throw new Error("body not inlined and no networkId");
  const res = await chrome.debugger.sendCommand(source, "Network.getRequestPostData", {
    requestId: networkId,
  });
  if (!res || typeof res.postData !== "string") throw new Error("empty getRequestPostData result");
  if (res.base64Encoded) return base64ToBytes(res.postData);
  if (res.postData.includes("�")) throw new Error("binary body not retrievable losslessly");
  return new TextEncoder().encode(res.postData);
}

// ─────────────────────────────────────────────────────────
// Request classification
// ─────────────────────────────────────────────────────────

const AZURE_SAFE_COMPS = new Set(["blocklist", "properties", "metadata", "lease", "list"]);
const AZURE_CHUNK_COMPS = new Set(["block", "appendblock", "page"]);

function classify(request, urlObj) {
  const path = urlObj.pathname.replace(/\/+$/, "");
  const method = request.method;
  const host = urlObj.hostname;

  if (method === "POST" && (path === "/backend-api/conversation" || path === "/backend-api/f/conversation")) {
    return "send";
  }
  // The composer sends the draft prompt text here (partial_query) BEFORE the real send.
  if (method === "POST" && path === "/backend-api/f/conversation/prepare") return "prepare";

  if ((method === "PUT" || method === "POST") &&
      (host.endsWith(".oaiusercontent.com") || host.endsWith(".blob.core.windows.net"))) {
    const comp = urlObj.searchParams.get("comp");
    if (comp && AZURE_SAFE_COMPS.has(comp)) return null;       // metadata-only Azure calls
    if (comp && AZURE_CHUNK_COMPS.has(comp)) return "file-chunk"; // partial bytes → can't mask
    return "file-put";
  }
  if (method === "POST" &&
      /^\/backend-api\/files(\/upload_reservations|\/process_upload_stream)?$/.test(path)) return "file-reserve";

  if (method === "POST" && host === "gemini.google.com" &&
      path.endsWith("BardFrontendService/StreamGenerate")) return "gemini-send";
  if ((method === "POST" || method === "PUT") && GEMINI_UPLOAD_HOSTS.has(host)) return "gemini-upload";
  return null;
}

// ─────────────────────────────────────────────────────────
// Handlers
// ─────────────────────────────────────────────────────────

async function maskStringSlots(slots, vaultId) {
  const types = new Set();
  const uncovered = new Set();
  let count = 0;
  let degraded = false;
  for (const slot of slots) {
    const original = slot.get();
    if (!original || !original.trim()) continue;
    const r = await maskViaBackend(original, vaultId);
    if (r.safe_to_send !== true || typeof r.masked_text !== "string") {
      const leaks = Array.isArray(r.leaks) ? r.leaks : [];
      const e = new Error(leaks.length ? "residual leak"
        : r.coverage_complete === false ? "Detection coverage incomplete (strict mode)"
        : "backend marked the text unsafe to send");
      e.leakTypes = leaks.map((l) => l && l.type).filter(Boolean);
      e.leakCount = leaks.length;
      throw e;
    }
    // (/api/mask doesn't return `pairs`; this is a no-op unless a backend adds them.)
    if (countResidualOriginals(r.masked_text, r.pairs) > 0) throw new Error("verification failed");
    slot.set(r.masked_text);
    count += r.entities_found || 0;
    (r.entity_types || []).forEach((t) => types.add(t));
    // Non-strict backends still say safe_to_send=true when e.g. the semantic model
    // (names/orgs/locations) wasn't ready. Don't hide that: record what was NOT checked.
    if (r.degraded === true || r.coverage_complete === false) {
      degraded = true;
      (r.uncovered_labels || []).forEach((t) => uncovered.add(t));
    }
  }
  return { types: [...types], count, degraded, uncovered: [...uncovered] };
}

const blockForMaskError = (source, requestId, err) =>
  block(source, requestId,
    err.leakCount ? "Residual scanner found leaks" : `Masking failed: ${err.message}`,
    err.leakTypes || [], err.leakCount || 0);

async function handleSend(source, params, body) {
  const { requestId } = params;
  const tabId = source.tabId;
  let parsed;
  try {
    parsed = JSON.parse(new TextDecoder().decode(body));
  } catch {
    return block(source, requestId, "Unparseable send body");
  }

  const textSlots = collectUserTextSlots(parsed);
  const nameSlots = collectAttachmentNameSlots(parsed);
  if (!textSlots.some((s) => s.get() && s.get().trim()) && nameSlots.length === 0) {
    return cont(source, requestId);
  }

  const vaultId = await resolveVaultId(tabId, parsed.conversation_id || null);
  let result;
  try {
    result = await maskStringSlots([...textSlots, ...nameSlots], vaultId);
  } catch (err) {
    return blockForMaskError(source, requestId, err);
  }

  try {
    await cont(source, requestId, { postData: encodeJson(parsed) });
  } catch (e) {
    console.error("[Doppel] continueRequest (masked) failed:", e.message);
    await block(source, requestId, "continueRequest failed");
    return;
  }
  trace({ kind: "send", outcome: "MASKED", detail: `${result.count} entities${result.degraded ? " (" + coverageNote(result.uncovered) + ")" : ""}` });
  await logActivity("mask", {
    entityTypes: result.types, entityCount: result.count,
    detail: result.degraded ? coverageNote(result.uncovered) : "",
  });
}

/** Draft prompt in /f/conversation/prepare → mask with the same vault as the send. */
async function handlePrepare(source, params, body) {
  const { requestId } = params;
  let parsed;
  try { parsed = JSON.parse(new TextDecoder().decode(body)); }
  catch { return block(source, requestId, "Unparseable prepare body"); }

  const slots = collectPrepareSlots(parsed);
  if (!slots.some((s) => s.get() && s.get().trim())) return cont(source, requestId);

  const vaultId = await resolveVaultId(source.tabId, parsed.conversation_id || null);
  let result;
  try { result = await maskStringSlots(slots, vaultId); }
  catch (err) { return blockForMaskError(source, requestId, err); }

  try {
    await cont(source, requestId, { postData: encodeJson(parsed) });
  } catch {
    return block(source, requestId, "continueRequest failed");
  }
  // Not counted as a separate "prompt" in stats (the real send is), trace only.
  trace({ kind: "prepare", outcome: "MASKED", detail: `${result.count} entities` });
}

/** Mask the file_name in an upload reservation and remember it for the PUT. */
async function handleReserve(source, params, body) {
  const { requestId } = params;
  const tabId = source.tabId;
  let parsed;
  try { parsed = JSON.parse(new TextDecoder().decode(body)); } catch { parsed = null; }
  if (!parsed || typeof parsed.file_name !== "string") {
    return cont(source, requestId);
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

  await cont(source, requestId, { postData: encodeJson(parsed) });
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function consumeAuthorizedBrowserFile(tabId, body) {
  const queue = authorizedMaskedFiles.get(tabId);
  if (!queue || queue.length === 0) return null;

  const now = Date.now();
  const hash = await sha256Hex(body);
  let matchIndex = -1;

  for (let i = 0; i < queue.length; i++) {
    const entry = queue[i];
    if (entry.expiresAt <= now) continue;
    if (!entry.hash) entry.hash = await sha256Hex(entry.bytes);
    if (entry.hash === hash) {
      matchIndex = i;
      break;
    }
  }

  // Remove expired entries and consume the matching authorization once.
  const remaining = queue.filter((entry, i) => entry.expiresAt > now && i !== matchIndex);
  if (remaining.length) authorizedMaskedFiles.set(tabId, remaining);
  else authorizedMaskedFiles.delete(tabId);

  if (matchIndex === -1) return null;
  return queue[matchIndex];
}

async function handleFilePut(source, params, body) {
  const { requestId, request } = params;
  const tabId = source.tabId;
  if (!body || body.length === 0) return cont(source, requestId); // genuinely empty

  const headers = request.headers || {};
  const contentType = getHeader(headers, "content-type") ||
    getHeader(headers, "x-ms-blob-content-type") || "application/octet-stream";
  const queue = reservedNames.get(tabId) || [];
  const filename = queue.shift() || "";

  // A browser-side interception may already have sent this exact masked file
  // through /api/process_file. Do not mask it a second time.
  try {
    const authorized = await consumeAuthorizedBrowserFile(tabId, body);
    if (authorized) {
      await cont(source, requestId);
      trace({
        kind: "file-put",
        outcome: "VERIFIED",
        detail: `browser-masked file verified (${body.length} bytes)`,
      });
      return;
    }
  } catch (err) {
    console.warn("[Doppel] Browser-file verification failed; falling back to network masking:", err.message);
  }

  let res;
  try {
    res = await maskFileChecked(body, filename, contentType, await resolveVaultId(tabId, null));
  } catch (err) {
    return block(source, requestId, `File masking failed: ${err.message}`);
  }

  try {
    await cont(source, requestId, { postData: bytesToBase64(res.bytes) });
  } catch (e) {
    console.error("[Doppel] continueRequest (file) failed:", e.message);
    await block(source, requestId, "continueRequest failed");
    return;
  }
  await recordFileMasked("file-put", body.length, res);
}

/**
 * Mask file bytes through the backend. The name handed to the backend gets an
 * extension that agrees with the content (the backend needs .docx/.xlsx on a
 * ZIP to treat it as Word/Excel). Throws if the backend refuses or returns nothing.
 */
/**
 * Reply to the page-side demasker: the fake→real entries of THIS tab's conversation.
 * Real values only ever travel backend → this worker → the tab's content script (isolated
 * world) and are written into the DOM as display text. Off switch: popup "Show real values".
 */
async function handleGetMapping(message, sender) {
  if (!sender.tab || !isChatGPTUrl(sender.tab.url)) {
    throw new BackendError("Mapping is only available on ChatGPT / Gemini", 400);
  }
  const st = await chrome.storage.local.get([PROTECTION_KEY, DEMASK_KEY]);
  if (st[PROTECTION_KEY] === false || st[DEMASK_KEY] === false) return { success: true, enabled: false };

  const vaultId = await resolveVaultId(sender.tab.id, null);
  const since = Number.isInteger(message.sinceVersion) ? message.sinceVersion : null;
  const resp = await backendPost("/api/mapping", () => ({
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: vaultId, since_version: since }),
  }), 5000);
  if (!resp.ok) throw new BackendError(`Mapping request failed (${resp.status})`, resp.status);
  const data = await resp.json();
  return { success: true, enabled: true, version: data.version, changed: !!data.changed,
           entries: Array.isArray(data.entries) ? data.entries : [] };
}

async function handleSelectedFile(message, sender) {
  const { [PROTECTION_KEY]: enabled } = await chrome.storage.local.get(PROTECTION_KEY);
  if (enabled === false) return { success: true, passthrough: true };

  if (!sender.tab || !isChatGPTUrl(sender.tab.url)) {
    throw new BackendError("File interception is only available on ChatGPT", 400);
  }
  if (typeof message.bytesBase64 !== "string" || !message.bytesBase64) {
    throw new BackendError("Missing file bytes", 400);
  }

  const binary = atob(message.bytesBase64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

  const vaultId = await resolveVaultId(sender.tab.id, null);
  const res = await maskFileChecked(
    bytes,
    typeof message.filename === "string" ? message.filename : "uploaded_file",
    typeof message.contentType === "string" ? message.contentType : "application/octet-stream",
    vaultId,
  );

  await recordFileMasked("browser-file", bytes.length, res);

  const tabId = sender.tab.id;
  let authQueue = authorizedMaskedFiles.get(tabId);
  if (!authQueue) {
    authQueue = [];
    authorizedMaskedFiles.set(tabId, authQueue);
  }
  const nowMs = Date.now();
  authQueue = authQueue.filter((e) => e.expiresAt > nowMs);   // drop stale authorizations
  authorizedMaskedFiles.set(tabId, authQueue);
  authQueue.push({
    hash: await sha256Hex(res.bytes),
    bytes: res.bytes,
    filename: message.filename || "uploaded_file",
    expiresAt: Date.now() + 5 * 60 * 1000,
  });

  return {
    success: true,
    bytesBase64: bytesToBase64(res.bytes),
    filename: fixFilename(message.filename || "uploaded_file", res.bytes, message.contentType),
    contentType: message.contentType || "application/octet-stream",
  };
}

async function maskFileChecked(bytes, filename, contentType, vaultId) {
  const name = fixFilename(filename, bytes, contentType);
  const res = await maskFileViaBackend(bytes, name, contentType, vaultId);
  if (!res.bytes || res.bytes.length === 0) throw new BackendError("Backend returned an empty file");
  return res;
}

async function recordFileMasked(kind, inBytes, res) {
  const note = res.degraded ? coverageNote(res.uncovered) : "";
  trace({ kind, outcome: "MASKED", detail: `${inBytes} -> ${res.bytes.length} bytes, ${res.replacements} replacements${note ? " (" + note + ")" : ""}` });
  await logActivity("file", { detail: note ? `File masked — ${note}` : "File masked", entityCount: res.replacements });
}

// ── Gemini ────────────────────────────────────────────────

async function handleGeminiSend(source, params, body) {
  const { requestId } = params;
  let state;
  try {
    state = parseGeminiBody(body);
  } catch (e) {
    return block(source, requestId, `Unrecognised Gemini request shape (${e.message})`);
  }
  const { text, names } = collectGeminiSlots(state);
  const vaultId = await resolveVaultId(source.tabId, geminiConversationId(state));
  let result;
  try {
    result = await maskStringSlots([...text, ...names], vaultId);
  } catch (err) {
    return blockForMaskError(source, requestId, err);
  }
  try {
    await cont(source, requestId, { postData: bytesToBase64(buildGeminiBody(state)) });
  } catch (e) {
    console.error("[Doppel] continueRequest (gemini) failed:", e.message);
    return block(source, requestId, "continueRequest failed");
  }
  trace({ kind: "gemini-send", outcome: "MASKED", detail: `${result.count} entities${result.degraded ? " (" + coverageNote(result.uncovered) + ")" : ""}` });
  await logActivity("mask", {
    entityTypes: result.types, entityCount: result.count,
    detail: result.degraded ? coverageNote(result.uncovered) : "",
  });
}

/** Resumable protocol step 1: body carries the filename (PII) — mask it and remember it. */
async function handleGeminiStart(source, params, body, headers) {
  const { requestId } = params;
  const tabId = source.tabId;
  const type = getHeader(headers, "x-goog-upload-header-content-type") || null;
  const name = parseGeminiStartName(body);
  const q = geminiUploads.get(tabId) || [];

  if (name === null) {
    // Metadata-only start without a recognisable name: no file bytes here, allow.
    q.push({ name: null, type });
    geminiUploads.set(tabId, q);
    trace({ kind: "gemini-upload", outcome: "start-unparsed" });
    return cont(source, requestId);
  }

  const { stem, ext } = splitFilename(name);
  let maskedName = name;
  const slot = { get: () => stem, set: (v) => { maskedName = v + ext; } };
  try {
    await maskStringSlots([slot], await resolveVaultId(tabId, null));
  } catch (err) {
    return block(source, requestId, `Filename masking failed: ${err.message}`);
  }
  q.push({ name: maskedName, type });
  if (q.length > 20) q.shift();
  geminiUploads.set(tabId, q);
  try {
    await cont(source, requestId, { postData: bytesToBase64(buildGeminiStartBody(maskedName)) });
  } catch {
    return block(source, requestId, "continueRequest failed");
  }
  trace({ kind: "gemini-upload", outcome: "start-masked" });
}

/** Resumable protocol final step: body is the raw file. */
async function handleGeminiFinalize(source, params, body, headers) {
  const { requestId } = params;
  const tabId = source.tabId;
  const offset = getHeader(headers, "x-goog-upload-offset");
  if (offset && offset.trim() !== "0") {
    // The file arrives in several pieces; no single piece is a parseable document.
    return block(source, requestId, "Chunked Gemini upload unsupported");
  }
  const entry = (geminiUploads.get(tabId) || []).shift() || {};
  const ct = getHeader(headers, "content-type");
  const contentType = (ct && !/^application\/x-www-form-urlencoded/i.test(ct) ? ct : null) ||
    entry.type || "application/octet-stream";
  let res;
  try {
    res = await maskFileChecked(body, entry.name || "", contentType, await resolveVaultId(tabId, null));
  } catch (err) {
    return block(source, requestId, `File masking failed: ${err.message}`);
  }
  try {
    await cont(source, requestId, { postData: bytesToBase64(res.bytes) });
  } catch {
    return block(source, requestId, "continueRequest failed");
  }
  await recordFileMasked("gemini-upload", body.length, res);
}

async function handleGeminiUpload(source, params, body) {
  const { requestId, request } = params;
  const headers = request.headers || {};
  const ct = getHeader(headers, "content-type") || "";
  const cmd = (getHeader(headers, "x-goog-upload-command") || "").trim().toLowerCase();
  const boundary = boundaryFromContentType(ct);

  if (cmd === "query" || cmd === "cancel") return cont(source, requestId); // no content
  if (!body || body.length === 0) return cont(source, requestId);

  // Single multipart/form-data upload carrying the file bytes.
  if (/^multipart\/form-data/i.test(ct) && boundary) {
    let parts;
    try { parts = parseMultipart(body, boundary); }
    catch (e) { return block(source, requestId, `Gemini upload parse failed (${e.message})`); }
    const filePart = parts.find((p) => p.filename !== null);
    if (!filePart) return cont(source, requestId);
    const vaultId = await resolveVaultId(source.tabId, null);
    const origName = filePart.filename || "upload";
    const inBytes = filePart.bytes.length;
    let res;
    try {
      res = await maskFileChecked(filePart.bytes, origName,
        filePart.contentType || "application/octet-stream", vaultId);
      const { stem, ext } = splitFilename(origName);
      await maskStringSlots([{ get: () => stem, set: (v) => { filePart.filename = v + ext; } }], vaultId);
    } catch (err) {
      return block(source, requestId, `File masking failed: ${err.message}`);
    }
    filePart.bytes = res.bytes;
    try {
      await cont(source, requestId, { postData: bytesToBase64(buildMultipart(parts, boundary)) });
    } catch {
      return block(source, requestId, "continueRequest failed");
    }
    await recordFileMasked("gemini-upload", inBytes, res);
    return;
  }

  if (cmd === "start") return handleGeminiStart(source, params, body, headers);
  if (cmd.includes("upload") && cmd.includes("finalize")) {
    return handleGeminiFinalize(source, params, body, headers);
  }

  trace({ kind: "gemini-upload", outcome: "UNSUPPORTED", detail: `content-type=${ct.split(";")[0]} x-goog-upload-command=${cmd || "-"}` });
  return block(source, requestId, "Unsupported Gemini upload format");
}

// ─────────────────────────────────────────────────────────
// CDP event router
// ─────────────────────────────────────────────────────────

chrome.debugger.onEvent.addListener(async (source, method, params) => {
  if (method !== "Fetch.requestPaused") return;
  const { requestId, request } = params;
  if (params.responseStatusCode !== undefined || params.responseErrorReason !== undefined) {
    try { await chrome.debugger.sendCommand(source, "Fetch.continueResponse", { requestId }); } catch {}
    return;
  }

  let kind = null;
  try {
    kind = classify(request, new URL(request.url));
  } catch { kind = null; }

  try {
    const h = new URL(request.url).hostname;
    if (request.method !== "GET" && request.method !== "OPTIONS") {
      console.debug(`[Doppel] ${request.method} ${h} -> ${kind || "passthrough"} hasBody=${!!request.hasPostData}`);
    }
  } catch {}

  if (kind) trace({ method: request.method, host: (() => { try { return new URL(request.url).hostname; } catch { return "?"; } })(), kind, outcome: "intercepted" });

  if (!kind) {
    try { await cont(source, requestId); } catch (e) { console.error("[Doppel] passthrough failed:", e.message); }
    return;
  }

  const { [PROTECTION_KEY]: enabled } = await chrome.storage.local.get(PROTECTION_KEY);
  if (enabled === false) {
    try { await cont(source, requestId); } catch {}
    return;
  }

  try {
    if (kind === "file-chunk") {
      return await block(source, requestId, "Chunked file upload unsupported");
    }
    const body = await getRequestBody(source, params);
    if (kind === "send") {
      if (!body) return cont(source, requestId);
      return await handleSend(source, params, body);
    }
    if (kind === "prepare") {
      if (!body) return cont(source, requestId);
      return await handlePrepare(source, params, body);
    }
    if (kind === "gemini-send") {
      if (!body) return block(source, requestId, "Gemini send without body");
      return await handleGeminiSend(source, params, body);
    }
    if (kind === "gemini-upload") return await handleGeminiUpload(source, params, body);
    if (kind === "file-reserve") {
      if (!body) return cont(source, requestId);
      return await handleReserve(source, params, body);
    }
    return await handleFilePut(source, params, body);
  } catch (err) {
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
        const targets = await chrome.debugger.getTargets();
        const mine = targets.find((t) => t.tabId === tabId && t.attached && t.extensionId === chrome.runtime.id);
        if (!mine) throw e;
      }
      await enableFetch(tabId);
      setDiag({ lastAttach: `tab ${tabId} OK at ${new Date().toLocaleTimeString()}` });
    } catch (err) {
      console.error(`[Doppel] Attach failed for tab ${tabId}:`, err.message);
      setDiag({ lastAttach: `tab ${tabId} FAILED: ${err.message}` });
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
  geminiUploads.delete(tabId);
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

chrome.tabs.onRemoved.addListener((tabId) => {
  authorizedMaskedFiles.delete(tabId);
  detachDebuggerFromTab(tabId);
});

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId === undefined) return;
  attachedTabs.delete(source.tabId);
  updateBadge(source.tabId);
  console.warn(`[Doppel] Debugger detached from tab ${source.tabId}: ${reason}`);
  setDiag({ lastDetach: `tab ${source.tabId}: ${reason} at ${new Date().toLocaleTimeString()}` });
});

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
        case "GET_MAPPING": {
          sendResponse(await handleGetMapping(message, sender));
          break;
        }
        case "MASK_SELECTED_FILE": {
          const result = await handleSelectedFile(message, sender);
          sendResponse(result);
          break;
        }


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
          if (!isChatGPTUrl(tab.url)) return sendResponse({ success: false, error: "Active tab is not ChatGPT or Gemini" });
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

        case "GET_DIAG": {
          const r = await chrome.storage.session.get(["trace", "diag"]);
          sendResponse({ success: true, trace: r.trace || [], diag: r.diag || {}, attached: [...attachedTabs] });
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

chrome.alarms.create("doppel-sync", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "doppel-sync") syncAllTabs().catch(() => {});
});

chrome.runtime.onInstalled.addListener(async () => {
  syncAllTabs().catch(() => {});
  await getToken();
  const cur = await chrome.storage.local.get([PROTECTION_KEY, "promptsMasked"]);
  if (cur.promptsMasked === undefined) {
    await chrome.storage.local.set({
      promptsMasked: 0, entitiesMasked: 0, filesMasked: 0, blocksCount: 0, activityLog: [],
    });
  }
  if (cur[PROTECTION_KEY] === undefined) await chrome.storage.local.set({ [PROTECTION_KEY]: true });
});

syncAllTabs().catch((e) => console.error("[Doppel] initial sync failed:", e.message));

console.log("[Doppel] Service worker v2.3 loaded");
