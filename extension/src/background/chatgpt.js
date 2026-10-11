import { bytesToBase64, collectAttachmentNameSlots, collectPrepareSlots, collectUserTextSlots, countResidualOriginals, fixFilename, splitFilename } from "./body.js";
import { DEMASK_KEY, authorizedMaskedFiles, encodeJson, getHeader, isChatGPTUrl, protectionOn, reservedNames, trace } from "./core.js";
import { BackendError, backendPost, coverageNote, logActivity, maskFileChecked, maskViaBackend, recordFileMasked } from "./backend.js";
import { resolveVaultId } from "./vault-id.js";
import { block, cont } from "./cdp.js";
// ─────────────────────────────────────────────────────────
// Handlers
// ─────────────────────────────────────────────────────────

export async function maskStringSlots(slots, vaultId) {
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

export const blockForMaskError = (source, requestId, err) =>
  block(source, requestId,
    err.leakCount ? "Residual scanner found leaks" : `Masking failed: ${err.message}`,
    err.leakTypes || [], err.leakCount || 0);

export async function handleSend(source, params, body) {
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
export async function handlePrepare(source, params, body) {
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
export async function handleReserve(source, params, body) {
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

export async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function consumeAuthorizedBrowserFile(tabId, body) {
  const queue = authorizedMaskedFiles.get(tabId);
  if (!queue || queue.length === 0) return null;

  const now = Date.now();
  const hash = await sha256Hex(body);
  let matchIndex = -1;

  for (let i = 0; i < queue.length; i++) {
    const entry = queue[i];
    if (entry.expiresAt <= now) continue;
    if (!entry.hash) continue;
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

export async function handleFilePut(source, params, body) {
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
export async function handleGetMapping(message, sender) {
  if (!sender.tab || !isChatGPTUrl(sender.tab.url)) {
    throw new BackendError("Mapping is only available on ChatGPT / Gemini", 400);
  }
  const st = await chrome.storage.local.get(DEMASK_KEY);
  if (!(await protectionOn()) || st[DEMASK_KEY] === false) return { success: true, enabled: false };

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

export async function handleSelectedFile(message, sender) {
  const enabled = await protectionOn();
  if (!enabled) return { success: true, passthrough: true };

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
    filename: message.filename || "uploaded_file",
    expiresAt: Date.now() + 5 * 60 * 1000,
  });
  authorizedMaskedFiles.set(tabId, authQueue);       // persist the push

  return {
    success: true,
    bytesBase64: bytesToBase64(res.bytes),
    filename: fixFilename(message.filename || "uploaded_file", res.bytes, message.contentType),
    contentType: message.contentType || "application/octet-stream",
  };
}
