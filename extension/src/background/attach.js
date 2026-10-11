import { FETCH_PATTERNS, attachedTabs, attaching, geminiUploads, isChatGPTUrl, pendingVaultId, protectionLocked, protectionOn, reservedNames, setDiag } from "./core.js";

// ─────────────────────────────────────────────────────────
// Attach / detach (+ recovery after service-worker restarts)
// ─────────────────────────────────────────────────────────

export async function enableFetch(tabId) {
  await chrome.debugger.sendCommand({ tabId }, "Network.enable");
  await chrome.debugger.sendCommand({ tabId }, "Fetch.enable", { patterns: FETCH_PATTERNS });
  attachedTabs.add(tabId);
}

export function attachDebuggerToTab(tabId) {
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

export async function detachDebuggerFromTab(tabId) {
  attachedTabs.delete(tabId);
  reservedNames.delete(tabId);
  geminiUploads.delete(tabId);
  pendingVaultId.delete(tabId);
  try { await chrome.debugger.detach({ tabId }); } catch { /* already detached */ }
  updateBadge(tabId);
}

export function updateBadge(tabId) {
  chrome.tabs.get(tabId).then((tab) => {
    if (!isChatGPTUrl(tab.url)) return chrome.action.setBadgeText({ tabId, text: "" });
    const on = attachedTabs.has(tabId);
    chrome.action.setBadgeText({ tabId, text: on ? "" : "!" });
    chrome.action.setBadgeBackgroundColor({ tabId, color: "#d93025" });
  }).catch(() => {});
}

export async function syncAllTabs() {
  const enabled = await protectionOn();
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!isChatGPTUrl(tab.url)) continue;
    if (!enabled) await detachDebuggerFromTab(tab.id);
    else await attachDebuggerToTab(tab.id);
  }
}


/** Chrome (or DevTools) dropped our debugger: put it back at once rather than waiting for the alarm.
 *  The one exception is the person pressing Cancel on Chrome's "debugging" bar, unless an admin locked
 *  protection on; fighting the user over their own browser would be wrong, and the "!" badge shows it. */
export async function reattachAfterDetach(tabId, reason) {
  if (reason === "target_closed") return;
  const locked = await protectionLocked();
  if (reason === "canceled_by_user" && !locked) return;
  if (!(await protectionOn())) return;
  let tab;
  try { tab = await chrome.tabs.get(tabId); } catch { return; }
  if (!isChatGPTUrl(tab.url)) return;
  for (let i = 0; i < 3 && !attachedTabs.has(tabId); i++) {
    await attachDebuggerToTab(tabId);
    if (!attachedTabs.has(tabId)) await new Promise((r) => setTimeout(r, 500 * (i + 1)));
  }
}
