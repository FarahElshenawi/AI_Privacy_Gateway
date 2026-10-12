import { describeBackendError, fixFilename } from "./body.js";
import { BACKEND_URL, FILE_TIMEOUT_MS, MASK_TIMEOUT_MS, MAX_ACTIVITY, TOKEN_KEY, trace } from "./core.js";

// ─────────────────────────────────────────────────────────
// Backend access
// ─────────────────────────────────────────────────────────
// Temporary diagnostics only. No file names, file contents, tokens, or PII are logged.
function timingNow() { return performance.now(); }
function timingLog(stage, elapsedMs, details = {}) {
  try {
    console.info("[Doppel Timing]", stage, {
      elapsedMs: Math.round(elapsedMs * 100) / 100,
      ...details,
    });
  } catch { /* diagnostics must never affect masking */ }
}

export async function fetchWithTimeout(url, opts, ms) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

export async function getToken() {
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
export class BackendError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

/**
 * POST to the backend with the install token. If the backend rejects the token
 * (401 — e.g. ~/.pii_gateway_token.json was regenerated), drop the cached token,
 * fetch a fresh one and retry ONCE. `makeInit` is a function so the body
 * (FormData) is rebuilt for the retry.
 */
export async function backendPost(path, makeInit, timeoutMs) {
  const isFileProcess = path === "/api/process_file";
  for (let attempt = 0; attempt < 2; attempt++) {
    const tokenStart = timingNow();
    const token = await getToken();
    if (isFileProcess) timingLog("background.get_token", timingNow() - tokenStart, { attempt: attempt + 1 });
    const init = makeInit();
    init.method = "POST";
    init.headers = { ...(init.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
    let resp;
    const httpStart = timingNow();
    try {
      resp = await fetchWithTimeout(`${BACKEND_URL}${path}`, init, timeoutMs);
      if (isFileProcess) timingLog("background.process_file_http_round_trip", timingNow() - httpStart, { attempt: attempt + 1, status: resp.status });
    } catch (e) {
      if (isFileProcess) timingLog("background.process_file_http_round_trip", timingNow() - httpStart, { attempt: attempt + 1, outcome: e.name === "AbortError" ? "timeout" : "error" });
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

export async function maskViaBackend(text, conversationId) {
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
export async function maskFileViaBackend(fileBytes, filename, contentType, conversationId) {
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
  const responseReadStart = timingNow();
  const responseBytes = new Uint8Array(await resp.arrayBuffer());
  timingLog("background.process_file_response_body_read", timingNow() - responseReadStart, { responseSizeBytes: responseBytes.byteLength });
  return {
    bytes: responseBytes,
    degraded: resp.headers.get("X-DLP-Degraded") === "true",
    uncovered: csv("X-DLP-Uncovered-Labels"),
    warnings: csv("X-DLP-Warnings"),
    replacements: parseInt(resp.headers.get("X-DLP-Replacements") || "0", 10) || 0,
  };
}

export const coverageNote = (labels) =>
  labels && labels.length ? `partial coverage — not checked: ${labels.join(", ")}` : "partial coverage";

// ─────────────────────────────────────────────────────────
// Activity log (no user content — types/counts only)
// ─────────────────────────────────────────────────────────

let logChain = Promise.resolve();
export function logActivity(type, details = {}) {
  logChain = logChain.then(() => doLogActivity(type, details)).catch(() => {});
  return logChain;
}

export async function doLogActivity(type, details) {
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

export async function maskFileChecked(bytes, filename, contentType, vaultId) {
  const name = fixFilename(filename, bytes, contentType);
  const res = await maskFileViaBackend(bytes, name, contentType, vaultId);
  if (!res.bytes || res.bytes.length === 0) throw new BackendError("Backend returned an empty file");
  return res;
}

export async function recordFileMasked(kind, inBytes, res) {
  const note = res.degraded ? coverageNote(res.uncovered) : "";
  trace({ kind, outcome: "MASKED", detail: `${inBytes} -> ${res.bytes.length} bytes, ${res.replacements} replacements${note ? " (" + note + ")" : ""}` });
  await logActivity("file", { detail: note ? `File masked — ${note}` : "File masked", entityCount: res.replacements });
}
