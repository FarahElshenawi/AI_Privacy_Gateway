/**
 * Doppel — AI Privacy Gateway
 * Service Worker v2.0 — chrome.debugger + CDP Fetch domain
 *
 * ARCHITECTURE
 * ============
 * Instead of monkey-patching `window.fetch` in the page's MAIN world
 * (which breaks React hydration, races ChatGPT's bundle, and gets bypassed
 * by Service Workers / Web Workers), we attach the Chrome DevTools Protocol
 * (CDP) debugger to chatgpt.com tabs and use CDP's `Fetch.enable` command
 * to intercept requests at the NETWORK layer — below the page, below the
 * page's SW, below the page's workers, and below the page's CSP.
 *
 * This is the same layer Charles Proxy, mitmproxy, and Fiddler operate at.
 * It's the architecturally correct way to intercept-and-modify HTTP bodies
 * from a browser extension.
 *
 * FLOW
 * ====
 * 1. chrome.tabs.onUpdated detects chatgpt.com navigation
 * 2. chrome.debugger.attach({tabId}, "1.3")
 * 3. Fetch.enable with URL pattern for /backend-api/conversation
 * 4. On Fetch.requestPaused event:
 *    a. Read params.request.postData (already a string — no body stream surgery)
 *    b. Parse JSON, extract user message text (handles both msg.role AND msg.author.role)
 *    c. POST to local backend /api/mask
 *    d. If success: rewrite body with masked text, Fetch.continueRequest
 *    e. If fail: Fetch.failRequest (fail-closed)
 *
 * TRADE-OFFS
 * ==========
 * - Yellow "Doppel is debugging this browser" banner at top of page
 *   (Chrome shows this automatically when chrome.debugger.attach is called)
 * - Slight latency (~5-10ms per request for CDP round-trip)
 * - SW may be killed during a paused request (handled with timeout)
 *
 * WHAT WE DON'T DO (yet)
 * =====================
 * - Demasking of streaming SSE responses. The user will see tokens like
 *   [PERSON_NAME_1] in the ChatGPT response. This is actually GOOD for the
 *   demo — it visibly proves masking worked. Demasking can be added later
 *   via Fetch.takeResponseBodyAsStream + a transform stream.
 */

const BACKEND_URL = "http://127.0.0.1:8765";
const TOKEN_KEY = "pii_gateway_token";
const PROTECTION_KEY = "protectionEnabled";
const MAX_ACTIVITY = 20;

// The actual message-send endpoint. We must EXCLUDE:
//   /conversation/init    — metadata (no user text)
//   /conversations?       — conversation list (no user text)
//   /f/conversation/prepare — pre-send validation (no user text)
// Match BOTH chatgpt.com and chat.openai.com (user may be on either)
const SEND_URL_PATTERNS = [
  "*://chatgpt.com/backend-api/conversation*",
  "*://chat.openai.com/backend-api/conversation*",
  "*://chatgpt.com/backend-api/f/conversation*",
  "*://chat.openai.com/backend-api/f/conversation*",
];

// File upload endpoints — ChatGPT uploads files before sending the prompt
// ChatGPT uses a 3-step flow:
//   1. POST /backend-api/files/upload_reservations → returns presigned URL on files.oaiusercontent.com
//   2. PUT directly to files.oaiusercontent.com (presigned URL) — THE ACTUAL FILE BYTES
//   3. POST /backend-api/files/upload_reservations/{id}/claim_and_finish
// NOTE: We use a BROAD pattern for oaiusercontent.com because the URL is a
//       presigned URL with a long, unpredictable query string.
const FILE_UPLOAD_PATTERNS = [
  "*://chatgpt.com/backend-api/files*",
  "*://chat.openai.com/backend-api/files*",
  "*://chatgpt.com/backend-api/uploads*",
  "*://chat.openai.com/backend-api/uploads*",
  "*://files.oaiusercontent.com/*",   // ← THE ACTUAL FILE UPLOAD (PUT bytes here)
  "*://*.oaiusercontent.com/*",      // ← Wildcard for subdomains
];

const ALL_INTERCEPT_PATTERNS = [...SEND_URL_PATTERNS, ...FILE_UPLOAD_PATTERNS];

// Track which tabs we've attached to, so we don't double-attach
const attachedTabs = new Set();

// Track in-flight mask operations so we can recover from SW restarts
const inFlight = new Map();

// Network domain request tracker — maps URL → networkRequestId
// (Fetch.requestPaused and Network.requestWillBeSent use different requestIds.
//  We need this mapping to call Network.getRequestPostData when
//  Fetch.getRequestPostData isn't available — Chrome 136+ removed it.)
const networkRequestByTab = new Map(); // tabId → Map<url, networkRequestId>

// ─────────────────────────────────────────────────────────
// Backend token management (unchanged from v1)
// ─────────────────────────────────────────────────────────

async function getToken() {
  const result = await chrome.storage.local.get(TOKEN_KEY);
  if (result[TOKEN_KEY]) return result[TOKEN_KEY];
  try {
    const resp = await fetch(`${BACKEND_URL}/token`);
    if (!resp.ok) return null;
    const data = await resp.json();
    if (data.token) {
      await chrome.storage.local.set({ [TOKEN_KEY]: data.token });
      return data.token;
    }
  } catch (err) {
    console.error("[Doppel] Failed to fetch token:", err);
  }
  return null;
}

async function makeAuthHeaders() {
  const token = await getToken();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

// ─────────────────────────────────────────────────────────
// Activity log (shared with popup)
// ─────────────────────────────────────────────────────────

async function logActivity(type, details) {
  const result = await chrome.storage.local.get([
    "activityLog", "promptsMasked", "entitiesMasked", "filesMasked", "blocksCount",
  ]);
  const log = result.activityLog || [];
  const now = new Date();
  const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  log.unshift({
    type, time: timeStr,
    entityTypes: details.entityTypes || [],
    entityCount: details.entityCount || 0,
    detail: details.detail || "",
  });
  if (log.length > MAX_ACTIVITY) log.length = MAX_ACTIVITY;

  const stats = {
    activityLog: log,
    promptsMasked: result.promptsMasked || 0,
    entitiesMasked: result.entitiesMasked || 0,
    filesMasked: result.filesMasked || 0,
    blocksCount: result.blocksCount || 0,
  };

  if (type === "mask") {
    stats.promptsMasked += 1;
    stats.entitiesMasked += details.entityCount || 0;
  } else if (type === "block") {
    stats.blocksCount += 1;
  } else if (type === "file") {
    stats.filesMasked += 1;
    stats.entitiesMasked += details.entityCount || 0;
  }

  await chrome.storage.local.set(stats);

  // Refresh popup if open. If popup is closed, sendMessage throws
  // "Could not establish connection. Receiving end does not exist."
  // — that's expected, silently ignore.
  try {
    await chrome.runtime.sendMessage({ type: "ACTIVITY_UPDATE" });
  } catch {
    // Popup not open — no-op
  }
}

// ─────────────────────────────────────────────────────────
// Body parsing — handles BOTH ChatGPT request shapes
// ─────────────────────────────────────────────────────────

function getUserRole(msg) {
  if (!msg) return null;
  if (msg.role) return msg.role;
  if (msg.author && msg.author.role) return msg.author.role;
  return null;
}

function extractUserText(bodyStr) {
  try {
    const parsed = JSON.parse(bodyStr);
    if (!parsed.messages || !Array.isArray(parsed.messages)) return null;

    const texts = [];
    for (const msg of parsed.messages) {
      if (getUserRole(msg) !== "user") continue;
      const content = msg.content;
      if (!content) continue;

      if (Array.isArray(content.parts)) {
        for (const part of content.parts) {
          if (typeof part === "string") texts.push(part);
          else if (part && typeof part === "object" && typeof part.text === "string") {
            texts.push(part.text);
          }
        }
      } else if (typeof content.text === "string") {
        texts.push(content.text);
      }
    }
    if (texts.length === 0) return null;
    return texts.join("\n");
  } catch { return null; }
}

function replaceUserText(bodyStr, originalText, maskedText) {
  try {
    const parsed = JSON.parse(bodyStr);
    if (!parsed.messages || !Array.isArray(parsed.messages)) return bodyStr;

    for (const msg of parsed.messages) {
      if (getUserRole(msg) !== "user") continue;
      const content = msg.content;
      if (!content) continue;

      if (Array.isArray(content.parts)) {
        for (let i = 0; i < content.parts.length; i++) {
          if (typeof content.parts[i] === "string") {
            content.parts[i] = content.parts[i].replace(originalText, maskedText);
          } else if (content.parts[i] && typeof content.parts[i] === "object" && typeof content.parts[i].text === "string") {
            content.parts[i].text = content.parts[i].text.replace(originalText, maskedText);
          }
        }
      } else if (typeof content.text === "string") {
        content.text = content.text.replace(originalText, maskedText);
      }
    }
    return JSON.stringify(parsed);
  } catch (e) {
    console.error("[Doppel] replaceUserText failed:", e);
    return bodyStr;
  }
}

// ─────────────────────────────────────────────────────────
// Backend mask call
// ─────────────────────────────────────────────────────────

async function maskViaBackend(text, conversationId) {
  const headers = await makeAuthHeaders();
  const resp = await fetch(`${BACKEND_URL}/api/mask`, {
    method: "POST",
    headers,
    body: JSON.stringify({ text, conversation_id: conversationId }),
  });
  if (!resp.ok) {
    return { success: false, error: `Backend ${resp.status}: ${await resp.text()}` };
  }
  const data = await resp.json();
  return { success: true, ...data };
}

// ─────────────────────────────────────────────────────────
// File masking — uses existing /api/process_file multimodal pipeline
// (PDF/Word/Excel/text — parser + reconstructor already built)
// ─────────────────────────────────────────────────────────

async function maskFileViaBackend(fileBytes, filename, contentType, conversationId) {
  const token = await getToken();
  const formData = new FormData();
  const blob = new Blob([fileBytes], { type: contentType || "application/octet-stream" });
  formData.append("file", blob, filename);
  if (conversationId) formData.append("conversation_id", conversationId);

  const resp = await fetch(`${BACKEND_URL}/api/process_file`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });

  if (!resp.ok) {
    return { success: false, error: `Backend ${resp.status}: ${await resp.text()}` };
  }
  const maskedBlob = await resp.blob();
  const maskedBytes = new Uint8Array(await maskedBlob.arrayBuffer());
  return { success: true, maskedBytes, maskedFilename: `masked_${filename}` };
}

// ─────────────────────────────────────────────────────────
// Multipart parsing & reconstruction (for intercepting file uploads)
// ─────────────────────────────────────────────────────────

function parseMultipart(bodyBytes, boundary) {
  // bodyBytes: Uint8Array of the multipart body
  // boundary: string (without --)
  // Returns array of { name, filename, contentBytes, contentType }
  const sep = new TextEncoder().encode(`--${boundary}`);

  // Split bodyBytes into parts on the separator
  const parts = [];
  let start = 0;
  while (true) {
    const idx = indexOfBytes(bodyBytes, sep, start);
    if (idx === -1) break;
    if (start > 0) {
      parts.push(bodyBytes.subarray(start, idx));
    }
    start = idx + sep.length;
  }
  if (start < bodyBytes.length) {
    parts.push(bodyBytes.subarray(start));
  }

  // Parse each part
  return parts.map(partBytes => {
    // Strip leading \r\n
    if (partBytes.length >= 2 && partBytes[0] === 0x0D && partBytes[1] === 0x0A) {
      partBytes = partBytes.subarray(2);
    }
    const headerSep = new TextEncoder().encode("\r\n\r\n");
    const headerEnd = indexOfBytes(partBytes, headerSep, 0);
    if (headerEnd === -1) return null;
    const headerStr = new TextDecoder().decode(partBytes.subarray(0, headerEnd));
    let contentBytes = partBytes.subarray(headerEnd + 4);
    // Strip trailing \r\n
    if (contentBytes.length >= 2 && contentBytes[contentBytes.length - 2] === 0x0D && contentBytes[contentBytes.length - 1] === 0x0A) {
      contentBytes = contentBytes.subarray(0, contentBytes.length - 2);
    }

    let name = null, filename = null, contentType = null;
    for (const line of headerStr.split("\r\n")) {
      const colon = line.indexOf(":");
      if (colon === -1) continue;
      const key = line.slice(0, colon).trim().toLowerCase();
      const val = line.slice(colon + 1).trim();
      if (key === "content-disposition") {
        const nameMatch = val.match(/name="([^"]*)"/);
        const fileMatch = val.match(/filename="([^"]*)"/);
        if (nameMatch) name = nameMatch[1];
        if (fileMatch) filename = fileMatch[1];
      }
      if (key === "content-type") contentType = val;
    }
    return { name, filename, contentBytes, contentType };
  }).filter(p => p !== null);
}

function indexOfBytes(haystack, needle, start) {
  for (let i = start; i <= haystack.length - needle.length; i++) {
    let match = true;
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) { match = false; break; }
    }
    if (match) return i;
  }
  return -1;
}

function buildMultipart(parts, boundary) {
  // parts: [{ name, filename, contentBytes, contentType }]
  // Returns: Uint8Array
  const encoder = new TextEncoder();
  const chunks = [];
  const crlf = encoder.encode("\r\n");

  for (const p of parts) {
    chunks.push(encoder.encode(`--${boundary}\r\n`));
    let disp = `Content-Disposition: form-data; name="${p.name}"`;
    if (p.filename) disp += `; filename="${p.filename}"`;
    chunks.push(encoder.encode(disp + "\r\n"));
    if (p.contentType) chunks.push(encoder.encode(`Content-Type: ${p.contentType}\r\n`));
    chunks.push(crlf);
    chunks.push(p.contentBytes);
    chunks.push(crlf);
  }
  chunks.push(encoder.encode(`--${boundary}--\r\n`));

  // Concatenate
  let total = 0;
  for (const c of chunks) total += c.length;
  const result = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    result.set(c, offset);
    offset += c.length;
  }
  return result;
}

// ─────────────────────────────────────────────────────────
// OAI upload handler — intercept PUT to files.oaiusercontent.com
// The PUT body is RAW file bytes (not multipart). Mask the file via the
// existing /api/process_file backend endpoint, then continueRequest with
// the masked bytes.
// ─────────────────────────────────────────────────────────

async function handleOAIUpload(source, requestId, request, url, bodyBytes, bodyStr) {
  console.log(`[Doppel] 📄 OAI PUT: ${url.slice(0, 100)}...`);

  // Determine file content type from request headers
  const contentType =
    request.headers?.["Content-Type"] ||
    request.headers?.["content-type"] ||
    request.headers?.["x-ms-blob-content-type"] ||
    "application/octet-stream";

  // Extract filename from URL query string (presigned URLs often include it)
  // If not in URL, derive from content-type (the PUT URL only has a UUID in the path)
  let filename = null;
  try {
    const urlObj = new URL(url);
    const fn = urlObj.searchParams.get("filename") ||
               urlObj.searchParams.get("file") ||
               urlObj.searchParams.get("blobName") ||
               urlObj.searchParams.get("name");
    if (fn) filename = fn;
  } catch {}

  // If we still don't have a filename, derive it from content-type
  // The PUT URL path is just /files/{uuid}/raw — no filename
  if (!filename) {
    const extByContentType = {
      "application/pdf": ".pdf",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
      "application/vnd.ms-excel": ".xls",
      "application/msword": ".doc",
      "text/plain": ".txt",
      "text/csv": ".csv",
      "text/markdown": ".md",
      "application/json": ".json",
      "image/png": ".png",
      "image/jpeg": ".jpg",
      "image/gif": ".gif",
      "image/webp": ".webp",
    };
    const ext = extByContentType[contentType] || "";
    filename = `uploaded_file${ext}`;
  }
  console.log(`[Doppel] Detected filename: ${filename} (from content-type: ${contentType})`);

  // Get the file bytes — prefer bodyBytes (binary-safe), fall back to bodyStr
  let fileBytes = bodyBytes;
  if (!fileBytes && bodyStr) {
    fileBytes = new TextEncoder().encode(bodyStr);
    console.log(`[Doppel] Body was string, encoded to ${fileBytes.length} bytes`);
  }
  if (!fileBytes || fileBytes.length === 0) {
    console.warn("[Doppel] No file body found — passing through");
    await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
    return;
  }

  console.log(`[Doppel] 📄 File: ${filename} (${fileBytes.length} bytes, ${contentType})`);

  const conversationId = `oai_${Date.now()}`;
  let maskResult;
  try {
    maskResult = await maskFileViaBackend(
      fileBytes,
      filename,
      contentType,
      conversationId
    );
  } catch (err) {
    console.error("[Doppel] OAI file masking threw:", err.message);
    await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
      requestId,
      errorReason: "BlockedByClient",
    });
    await logActivity("block", { detail: `OAI file mask error: ${err.message}` });
    return;
  }

  if (!maskResult.success) {
    console.error("[Doppel] OAI file masking failed — blocking:", maskResult.error);
    await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
      requestId,
      errorReason: "BlockedByClient",
    });
    await logActivity("block", { detail: `OAI file mask failed: ${maskResult.error}` });
    return;
  }

  console.log(`[Doppel] ✅ Masked file received (${maskResult.maskedBytes.length} bytes). Replacing PUT body.`);

  await logActivity("file", {
    entityCount: 1,
    detail: filename,
  });

  // Continue the PUT with the masked file bytes (base64-encoded for CDP)
  const maskedBase64 = utf8ToBase64FromBytes(maskResult.maskedBytes);
  console.log(`[Doppel] Encoded masked file as base64 (${maskResult.maskedBytes.length} → ${maskedBase64.length})`);

  try {
    await chrome.debugger.sendCommand(source, "Fetch.continueRequest", {
      requestId,
      postData: maskedBase64,
    });
    console.log("[Doppel] ✅ continueRequest with masked OAI file succeeded");
  } catch (e) {
    console.error("[Doppel] ❌ continueRequest (OAI masked) failed:", e.message);
    try {
      await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
        requestId,
        errorReason: "Failed",
      });
    } catch {}
  }
}

// ─────────────────────────────────────────────────────────
// Network event tracker — maps URLs to Network requestIds
// ─────────────────────────────────────────────────────────

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (method === "Network.requestWillBeSent") {
    // Track this request so we can look it up later by URL
    const tabId = source.tabId;
    if (!networkRequestByTab.has(tabId)) {
      networkRequestByTab.set(tabId, new Map());
    }
    const urlMap = networkRequestByTab.get(tabId);
    // Use the full URL as key (with query string, since presigned URLs differ by sig)
    urlMap.set(params.request.url, params.requestId);
    // Clean up old entries (keep map size reasonable)
    if (urlMap.size > 100) {
      const firstKey = urlMap.keys().next().value;
      urlMap.delete(firstKey);
    }
  }
});

async function getNetworkPostData(tabId, url) {
  // Look up the Network requestId for this URL
  const urlMap = networkRequestByTab.get(tabId);
  if (!urlMap) return null;
  const networkRequestId = urlMap.get(url);
  if (!networkRequestId) {
    console.log(`[Doppel] No Network requestId found for URL: ${url.slice(0, 80)}...`);
    return null;
  }
  try {
    const result = await chrome.debugger.sendCommand(
      { tabId },
      "Network.getRequestPostData",
      { requestId: networkRequestId }
    );
    if (result && result.postData) {
      console.log(`[Doppel] ✅ Got body via Network.getRequestPostData (len=${result.postData.length})`);
      return result.postData;
    }
    return null;
  } catch (e) {
    console.error("[Doppel] Network.getRequestPostData failed:", e.message);
    return null;
  }
}

// ─────────────────────────────────────────────────────────
// CDP Fetch interception — the core of v2.0
// ─────────────────────────────────────────────────────────

async function attachDebuggerToTab(tabId) {
  if (attachedTabs.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
    console.log(`[Doppel] Attached to tab ${tabId}. Enabling Fetch.enable + Network.enable:`);
    SEND_URL_PATTERNS.forEach(p => console.log(`[Doppel]   pattern: ${p}`));

    // Enable Network domain (needed to retrieve request bodies via
    // Network.getRequestPostData — Fetch.getRequestPostData was removed
    // in Chrome 136+)
    await chrome.debugger.sendCommand({ tabId }, "Network.enable");

    await chrome.debugger.sendCommand({ tabId }, "Fetch.enable", {
      patterns: ALL_INTERCEPT_PATTERNS.map(urlPattern => ({
        urlPattern,
        requestStage: "Request",
      })),
    });
    attachedTabs.add(tabId);
    console.log(`[Doppel] ✅ Fetch.enable + Network.enable active on tab ${tabId}`);
  } catch (err) {
    console.error(`[Doppel] ❌ Attach failed for tab ${tabId}:`, err.message);
  }
}

async function detachDebuggerFromTab(tabId) {
  if (!attachedTabs.has(tabId)) return;
  try {
    await chrome.debugger.detach({ tabId });
  } catch (e) {
    // Already detached
  }
  attachedTabs.delete(tabId);
  console.log(`[Doppel] Debugger detached from tab ${tabId}`);
}

// ─────────────────────────────────────────────────────────
// CDP event handler — fires when a request matches our pattern
// ─────────────────────────────────────────────────────────

chrome.debugger.onEvent.addListener(async (source, method, params) => {
  if (method !== "Fetch.requestPaused") return;

  const { tabId } = source;
  const { requestId, request } = params;
  const url = request.url;

  // Log EVERY paused request so we can see what CDP is catching
  console.log(`[Doppel] 🎯 requestPaused: ${request.method} ${url.slice(0, 120)}`);

  // Filter: only intercept the actual message-send endpoint.
  let urlObj;
  try { urlObj = new URL(url); } catch { urlObj = null; }
  const pathname = urlObj?.pathname || "";

  // The actual send endpoint. Use startsWith to be tolerant of trailing slashes
  // or path variations like /backend-api/conversation/abc-123.
  const isSendEndpoint =
    pathname === "/backend-api/conversation" ||
    pathname.startsWith("/backend-api/conversation/") ||
    pathname === "/backend-api/f/conversation" ||
    pathname.startsWith("/backend-api/f/conversation/");

  // File upload endpoints
  // ChatGPT file uploads come in three flavors:
  //   1. /backend-api/files/upload_reservations (POST, JSON, metadata only)
  //   2. /backend-api/files/upload_reservations/{id}/claim_and_finish (POST, JSON)
  //   3. PUT to files.oaiusercontent.com — THE ACTUAL FILE BYTES (this is what we mask!)
  const isOAIUpload = urlObj?.hostname?.endsWith(".oaiusercontent.com") && request.method === "PUT";
  const isFileUploadApi =
    pathname.startsWith("/backend-api/files") ||
    pathname.startsWith("/backend-api/uploads");

  if (!isSendEndpoint && !isOAIUpload && !isFileUploadApi) {
    // Not interesting — pass through unchanged
    console.log(`[Doppel] ↩️ Passthrough (not send/upload/oai): ${pathname}`);
    try {
      await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
    } catch (e) {
      console.error("[Doppel] continueRequest (passthrough) failed:", e.message);
    }
    return;
  }

  console.log(`[Doppel] ✋ Intercepted ${isOAIUpload ? "OAI UPLOAD" : isFileUploadApi ? "FILE API" : "SEND"}: ${url}`);

  // ─── Body retrieval ───
  // CDP's request.postData is ONLY populated for small inlined bodies.
  // For streamed uploads or large bodies, postData is undefined and we MUST
  // call Fetch.getRequestPostData to retrieve it as a separate async command.
  // GET requests have no body — skip the lookup entirely (CDP doesn't expose
  // getRequestPostData for them, would log a noisy error).
  //
  // IMPORTANT for binary uploads: CDP's postData string is UTF-8 decoded.
  // For binary content (PDFs, images), we MUST get raw bytes via
  // getRequestPostData (which returns base64) to avoid corruption.
  let bodyStr = request.postData;  // string version (text bodies only)
  let bodyBytes = null;            // raw bytes version (binary bodies)

  if (request.method === "GET" || request.method === "HEAD") {
    // GET/HEAD have no body — pass through silently
    try {
      await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
    } catch (e) {
      console.error("[Doppel] continueRequest (GET passthrough) failed:", e.message);
    }
    return;
  }

  // For OAI PUT (actual file bytes) and file API calls, ALWAYS use getRequestPostData
  // to get raw bytes (the inlined postData string corrupts binary content)
  if (isOAIUpload || isFileUploadApi) {
    // BUT — if postData is already inlined in the event, use it directly
    // (getRequestPostData fails on requests whose body was already inlined)
    if (request.postData !== undefined && request.postData !== null) {
      // Body inlined as string (JSON metadata, not binary file)
      bodyStr = request.postData;
      console.log(`[Doppel] Body inlined in event (len=${bodyStr.length})`);
    } else {
      // Body NOT inlined — try Fetch.getRequestPostData first (old Chrome)
      // then fall back to Network.getRequestPostData (Chrome 136+)
      let postDataStr = null;
      try {
        const postResult = await chrome.debugger.sendCommand(source, "Fetch.getRequestPostData", {
          requestId,
        });
        if (postResult && typeof postResult.postData === "string") {
          postDataStr = postResult.postData;
          console.log(`[Doppel] ✅ Got body via Fetch.getRequestPostData (len=${postDataStr.length})`);
        }
      } catch (e) {
        console.log("[Doppel] Fetch.getRequestPostData unavailable, trying Network.getRequestPostData...");
      }

      // Fall back to Network domain (Chrome 136+ removed Fetch.getRequestPostData)
      if (!postDataStr) {
        postDataStr = await getNetworkPostData(tabId, url);
      }

      if (postDataStr) {
        // Try base64 decode first (binary content like PDFs)
        try {
          bodyBytes = base64ToBytes(postDataStr);
          bodyStr = null;
          console.log(`[Doppel] ✅ Decoded body as base64 → ${bodyBytes.length} bytes`);
        } catch (e) {
          // Not base64 — treat as plain string (JSON metadata)
          bodyStr = postDataStr;
          bodyBytes = null;
          console.log(`[Doppel] ✅ Body is plain text (len=${bodyStr.length})`);
        }
      } else {
        // Can't get body — pass through (better than blocking)
        console.warn("[Doppel] Could not get request body — passing through");
        try {
          await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
        } catch {}
        return;
      }
    }
  } else if (bodyStr === undefined || bodyStr === null) {
    // Text send with no inlined body — try getRequestPostData
    try {
      const postResult = await chrome.debugger.sendCommand(source, "Fetch.getRequestPostData", {
        requestId,
      });
      if (postResult && typeof postResult.postData === "string") {
        bodyStr = postResult.postData;
        console.log(`[Doppel] ✅ Got body via getRequestPostData (text, len=${bodyStr.length})`);
      } else {
        // POST with no body — pass through
        try {
          await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
        } catch {}
        return;
      }
    } catch (e) {
      console.error("[Doppel] getRequestPostData failed:", e.message);
      try {
        await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
      } catch {}
      return;
    }
  } else {
    console.log(`[Doppel] Body inlined in event (len=${bodyStr.length})`);
  }

  // No body? Pass through
  if (!bodyStr && !bodyBytes) {
    try {
      await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
    } catch (e) {
      console.error("[Doppel] continueRequest (empty body) failed:", e.message);
    }
    return;
  }

  // ═══════════════════════════════════════════════════════════════
  // FILE API BRANCH — metadata calls (upload_reservations, claim_and_finish)
  // Pass through unchanged — they don't contain file bytes, just JSON metadata.
  // ═══════════════════════════════════════════════════════════════
  if (isFileUploadApi) {
    console.log("[Doppel] File API metadata call — passing through");
    try {
      await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
    } catch (e) {
      console.error("[Doppel] continueRequest (file API) failed:", e.message);
    }
    return;
  }

  // ═══════════════════════════════════════════════════════════════
  // OAI UPLOAD BRANCH — actual file bytes uploaded to files.oaiusercontent.com
  // This is the PUT that carries the real PDF/Word/Excel file content.
  // ═══════════════════════════════════════════════════════════════
  if (isOAIUpload) {
    try {
      await handleOAIUpload(source, requestId, request, url, bodyBytes, bodyStr);
    } catch (err) {
      console.error("[Doppel] OAI upload handler threw:", err.message);
      try {
        await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
          requestId,
          errorReason: "Failed",
        });
      } catch {}
    }
    return;
  }

  // ═══════════════════════════════════════════════════════════════
  // TEXT SEND BRANCH — extract user text, mask, rewrite body
  // ═══════════════════════════════════════════════════════════════
  const originalText = extractUserText(bodyStr);

  if (!originalText || !originalText.trim()) {
    // No user text to mask — pass through unchanged
    console.log("[Doppel] No user text extracted — passing through. Body first 200 chars:", bodyStr.slice(0, 200));
    try {
      await chrome.debugger.sendCommand(source, "Fetch.continueRequest", { requestId });
    } catch (e) {
      console.error("[Doppel] continueRequest (no text) failed:", e.message);
    }
    return;
  }

  console.log("[Doppel] User text (first 100 chars):", originalText.slice(0, 100));

  const conversationId = `tab${tabId}_conv_${Date.now()}`;
  inFlight.set(requestId, { tabId, conversationId, startedAt: Date.now() });

  let maskResult;
  try {
    maskResult = await maskViaBackend(originalText, conversationId);
  } catch (err) {
    console.error("[Doppel] Backend mask call threw:", err.message);
    maskResult = { success: false, error: err.message };
  }

  inFlight.delete(requestId);

  // Fail-closed: if masking failed, block the request
  if (!maskResult.success) {
    console.error("[Doppel] Masking failed — blocking request:", maskResult.error);
    await logActivity("block", { detail: `Masking failed: ${maskResult.error}` });
    try {
      await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
        requestId,
        errorReason: "BlockedByClient",
      });
    } catch (e) {
      console.error("[Doppel] failRequest failed:", e.message);
    }
    return;
  }

  // Fail-closed: if residual scanner found leaks, block
  if (!maskResult.safe_to_send) {
    console.error("[Doppel] Residual scanner found leaks — blocking:", maskResult.leaks);
    await logActivity("block", {
      detail: "Residual scanner found leaks",
      entityTypes: (maskResult.leaks || []).map(l => l.type),
      entityCount: (maskResult.leaks || []).length,
    });
    try {
      await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
        requestId,
        errorReason: "BlockedByClient",
      });
    } catch (e) {
      console.error("[Doppel] failRequest (leak) failed:", e.message);
    }
    return;
  }

  // Success — rewrite the body with masked text
  const newBody = replaceUserText(bodyStr, originalText, maskResult.masked_text);

  console.log(`[Doppel] ✅ Masked ${maskResult.entities_found} entities. Forwarding.`);
  console.log("[Doppel] Original body (first 300):", bodyStr.slice(0, 300));
  console.log("[Doppel] Masked body (first 300):  ", newBody.slice(0, 300));
  console.log("[Doppel] Bodies differ:", bodyStr !== newBody);

  const entityTypes = [...new Set((maskResult.pairs || []).map(p => p[0]))];
  await logActivity("mask", {
    entityTypes,
    entityCount: maskResult.entities_found || 0,
  });

  // CRITICAL: CDP's Fetch.continueRequest expects `postData` as BASE64, not
  // a plain string. If we pass a string, CDP throws:
  //   "Failed to deserialize params.postData - BINDINGS: invalid base64 string"
  // We must encode the body as UTF-8 bytes, then base64-encode those bytes.
  // (Plain btoa() only handles Latin1 — would throw on Unicode.)
  const postDataBase64 = utf8ToBase64(newBody);
  console.log(`[Doppel] Encoded body as base64 (len=${newBody.length} → ${postDataBase64.length})`);

  try {
    await chrome.debugger.sendCommand(source, "Fetch.continueRequest", {
      requestId,
      postData: postDataBase64,
    });
    console.log("[Doppel] ✅ continueRequest with masked body succeeded");
  } catch (e) {
    console.error("[Doppel] ❌ continueRequest (masked) failed:", e.message);
    // If continueRequest fails, the request is stuck. Try to fail it.
    try {
      await chrome.debugger.sendCommand(source, "Fetch.failRequest", {
        requestId,
        errorReason: "Failed",
      });
    } catch {}
  }
});

// ─────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────

/**
 * Encode a UTF-8 string as base64.
 * (Plain btoa() only handles Latin1 — this handles full Unicode safely.)
 */
function utf8ToBase64(str) {
  const bytes = new TextEncoder().encode(str);
  return utf8ToBase64FromBytes(bytes);
}

/**
 * Encode a Uint8Array as base64. Used for binary file uploads where the
 * body is already in bytes (not a string).
 */
function utf8ToBase64FromBytes(bytes) {
  let binary = "";
  const chunkSize = 0x8000; // avoid call stack limits on large strings
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode.apply(null, chunk);
  }
  return btoa(binary);
}

/**
 * Decode a base64 string to a Uint8Array.
 * CDP's Fetch.getRequestPostData returns postData as base64 for binary content.
 */
function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// ─────────────────────────────────────────────────────────
// Tab lifecycle — auto-attach when user opens chatgpt.com
// ─────────────────────────────────────────────────────────

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  // Check if protection is enabled
  const { protectionEnabled } = await chrome.storage.local.get(PROTECTION_KEY);
  if (protectionEnabled === false) return; // explicitly disabled

  if (!tab.url) return;
  const isChatGPT = tab.url.includes("chatgpt.com") || tab.url.includes("chat.openai.com");
  if (!isChatGPT) return;

  // Attach as soon as we know it's chatgpt.com — don't wait for "complete"
  // (the user might send a prompt before "complete" fires)
  if (changeInfo.status === "loading" || changeInfo.status === "complete") {
    if (!attachedTabs.has(tabId)) {
      console.log(`[Doppel] Tab ${tabId} navigated to ChatGPT (status=${changeInfo.status}). Attaching...`);
      await attachDebuggerToTab(tabId);
    }
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  await detachDebuggerFromTab(tabId);
});

// If user navigates AWAY from chatgpt.com, detach
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.url) {
    const isChatGPT = tab.url && (tab.url.includes("chatgpt.com") || tab.url.includes("chat.openai.com"));
    if (!isChatGPT && attachedTabs.has(tabId)) {
      await detachDebuggerFromTab(tabId);
    }
  }
});

// When debugger is detached (by user closing DevTools, or by us), clean up
chrome.debugger.onDetach.addListener(async (source) => {
  const tabId = source.tabId;
  attachedTabs.delete(tabId);
  console.log(`[Doppel] Debugger detached from tab ${tabId} (external)`);
});

// ─────────────────────────────────────────────────────────
// Message handler — for popup communication
// ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      switch (message.type) {
        case "PING":
          sendResponse({ success: true, pong: Date.now(), sw: "v2.0 alive" });
          break;

        case "HEALTH": {
          try {
            const resp = await fetch(`${BACKEND_URL}/health`);
            if (resp.ok) {
              const data = await resp.json();
              sendResponse({ success: true, status: data.status });
            } else {
              sendResponse({ success: false, status: `HTTP ${resp.status}` });
            }
          } catch {
            sendResponse({ success: false, status: "offline" });
          }
          break;
        }

        case "GET_DEBUGGER_STATUS": {
          // Return list of tabs we're attached to
          const tabs = [];
          for (const tabId of attachedTabs) {
            try {
              const tab = await chrome.tabs.get(tabId);
              tabs.push({ tabId, url: tab.url, title: tab.title });
            } catch {
              // Tab may have been closed
              attachedTabs.delete(tabId);
            }
          }
          sendResponse({ success: true, attachedTabs: tabs, count: tabs.length });
          break;
        }

        case "ATTACH_NOW": {
          // Manual attach request from popup
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab) {
            sendResponse({ success: false, error: "No active tab" });
            return;
          }
          if (!tab.url?.includes("chatgpt.com") && !tab.url?.includes("chat.openai.com")) {
            sendResponse({ success: false, error: "Active tab is not chatgpt.com" });
            return;
          }
          await attachDebuggerToTab(tab.id);
          sendResponse({ success: true, tabId: tab.id });
          break;
        }

        case "DETACH_NOW": {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          if (!tab) {
            sendResponse({ success: false, error: "No active tab" });
            return;
          }
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
  return true; // keep channel open for async sendResponse
});

// ─────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(async () => {
  console.log("[Doppel] Installed — fetching token...");
  await getToken();
  await chrome.storage.local.set({
    promptsMasked: 0,
    entitiesMasked: 0,
    filesMasked: 0,
    blocksCount: 0,
    activityLog: [],
    protectionEnabled: true,
  });
});

// On SW startup, try to attach to any existing chatgpt.com tabs
chrome.tabs.query({}).then((tabs) => {
  for (const tab of tabs) {
    if (tab.url && (tab.url.includes("chatgpt.com") || tab.url.includes("chat.openai.com"))) {
      attachDebuggerToTab(tab.id);
    }
  }
});

console.log("[Doppel] Service worker v2.0 loaded (chrome.debugger + CDP Fetch domain)");
