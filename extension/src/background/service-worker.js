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

import { BACKEND_URL, LOCK_KEY, PROTECTION_KEY, TOKEN_KEY, attachedTabs, authorizedMaskedFiles, isChatGPTUrl, protectionOn, setDiag, stateReady, trace } from "./core.js";
import { fetchWithTimeout, getToken } from "./backend.js";
import { block, classify, cont, getRequestBody } from "./cdp.js";
import { handleFilePut, handleGetMapping, handlePrepare, handleReserve, handleSelectedFile, handleSend } from "./chatgpt.js";
import { handleGeminiSend, handleGeminiUpload } from "./gemini.js";
import { attachDebuggerToTab, detachDebuggerFromTab, reattachAfterDetach, syncAllTabs, updateBadge } from "./attach.js";

// ─────────────────────────────────────────────────────────
// CDP event router
// ─────────────────────────────────────────────────────────

chrome.debugger.onEvent.addListener(async (source, method, params) => {
  if (method !== "Fetch.requestPaused") return;
  await stateReady;            // a restarted worker must know its per-tab queues before judging a request
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

  const enabled = await protectionOn();
  if (!enabled) {
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

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab.url;
  if (!url) return;
  if (!isChatGPTUrl(url)) {
    if (changeInfo.url && attachedTabs.has(tabId)) await detachDebuggerFromTab(tabId);
    return;
  }
  const enabled = await protectionOn();
  if (!enabled) return;
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
  reattachAfterDetach(source.tabId, reason).catch(() => {});
});


chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[PROTECTION_KEY]) syncAllTabs();
  if (area === "managed" && changes[LOCK_KEY]) syncAllTabs();
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  protectionOn().then((on) => on && !attachedTabs.has(tabId) && chrome.tabs.get(tabId)
    .then((t) => isChatGPTUrl(t.url) && attachDebuggerToTab(tabId))).catch(() => {});
});

// ─────────────────────────────────────────────────────────
// Popup messages
// ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      await stateReady;
      // The content scripts talk to us constantly. If the tab lost its debugger (Chrome dropped it, or this
      // worker was restarted), take that as a cue to re-attach now instead of waiting for the next alarm.
      if (sender && sender.tab && isChatGPTUrl(sender.tab.url) && !attachedTabs.has(sender.tab.id)
          && message.type !== "DETACH_NOW" && await protectionOn()) {
        attachDebuggerToTab(sender.tab.id).catch(() => {});
      }
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
