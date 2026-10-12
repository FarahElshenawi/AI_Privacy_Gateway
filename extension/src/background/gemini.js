import { boundaryFromContentType, buildGeminiBody, buildGeminiStartBody, buildMultipart, bytesToBase64, collectGeminiSlots, geminiConversationId, parseGeminiBody, parseGeminiStartName, parseMultipart, splitFilename } from "./body.js";
import { geminiUploads, getHeader, trace } from "./core.js";
import { coverageNote, logActivity, maskFileChecked, recordFileMasked } from "./backend.js";
import { resolveVaultId } from "./vault-id.js";
import { block, cont } from "./cdp.js";
import { blockForMaskError, maskStringSlots } from "./chatgpt.js";
// ── Gemini ────────────────────────────────────────────────

export async function handleGeminiSend(source, params, body) {
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
export async function handleGeminiStart(source, params, body, headers) {
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
export async function handleGeminiFinalize(source, params, body, headers) {
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

export async function handleGeminiUpload(source, params, body) {
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
