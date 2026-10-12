import { base64ToBytes, concatBytes } from "./body.js";
import { GEMINI_UPLOAD_HOSTS, trace } from "./core.js";
import { logActivity } from "./backend.js";
// ─────────────────────────────────────────────────────────
// CDP helpers
// ─────────────────────────────────────────────────────────

export const cont = (source, requestId, extra = {}) =>
  chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId, ...extra });

export async function block(source, requestId, reason, entityTypes = [], entityCount = 0) {
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
export async function getRequestBody(source, params) {
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

export const AZURE_SAFE_COMPS = new Set(["blocklist", "properties", "metadata", "lease", "list"]);
export const AZURE_CHUNK_COMPS = new Set(["block", "appendblock", "page"]);

export function classify(request, urlObj) {
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
