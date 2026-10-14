import { convIdFromUrl } from "./body.js";
import { SESSION_KEY_VAULT_IDS, pendingVaultId, setDiag } from "./core.js";

// ─────────────────────────────────────────────────────────
// Vault id: stable per conversation (shared by prompts AND files)
// ─────────────────────────────────────────────────────────

export async function tabConvId(tabId) {
  try { return convIdFromUrl((await chrome.tabs.get(tabId)).url); } catch { return null; }
}

// Several things ask for a tab's vault at the same moment (the send, the draft "prepare", the page's
// mapping request), and a new chat gets its server id right then. Each lookup reads the stored map,
// decides, and writes it back; two overlapping lookups would both read the old map, one would take the
// pending id and the other would invent `conv_<id>`, and the second write would win. The first message
// would then be masked under one vault and looked up under another, so nothing is ever restored.
// Run them one at a time.
let queue = Promise.resolve();
export function resolveVaultId(tabId, serverConvId) {
  const run = queue.then(() => resolveVaultIdNow(tabId, serverConvId));
  queue = run.catch(() => {});
  return run;
}

const PENDING = (tabId) => `__pending_${tabId}`;      // a new chat's vault, until the chat has a server id
const RECENT = (tabId) => `__recent_${tabId}`;        // vaults this tab used lately (newest first)
const RECENT_MAX = 8;

// Everything about a tab's vaults lives in ONE stored record, read and written together, so the
// "new chat" id and the binding to its conversation id can never disagree.
async function resolveVaultIdNow(tabId, serverConvId) {
  // File uploads carry no conversation id in their bodies; the tab URL does
  // (/c/<id>, /app/<id>). Without this, a file uploaded into an existing chat
  // got a different vault than the prompts → inconsistent surrogates.
  serverConvId = serverConvId || await tabConvId(tabId);
  const store = (await chrome.storage.session.get(SESSION_KEY_VAULT_IDS))[SESSION_KEY_VAULT_IDS] || {};
  const pend = store[PENDING(tabId)] || pendingVaultId.get(tabId);
  let id, how;
  if (serverConvId) {
    id = store[serverConvId];
    if (id) {
      how = "known";
    } else {
      id = pend || `conv_${serverConvId}`;
      how = pend ? "bound-new-chat" : "no-pending-fallback";
      store[serverConvId] = id;
    }
    if (how === "bound-new-chat") {
      delete store[PENDING(tabId)];
      pendingVaultId.delete(tabId);
    }
  } else if (pend) {
    id = pend;
    how = "reuse-new-chat";
  } else {
    id = `new_${tabId}_${crypto.randomUUID()}`;
    how = "new-chat";
    store[PENDING(tabId)] = id;
  }
  if (!serverConvId) pendingVaultId.set(tabId, id);
  const recent = store[RECENT(tabId)] || [];
  if (recent[0] !== id) store[RECENT(tabId)] = [id, ...recent.filter((x) => x !== id)].slice(0, RECENT_MAX);
  await chrome.storage.session.set({ [SESSION_KEY_VAULT_IDS]: store });
  if (how !== "known" && how !== "reuse-new-chat") {
    setDiag({ lastVaultDecision: `tab ${tabId}: ${how}${serverConvId ? " (chat has an id)" : ""} at ${new Date().toLocaleTimeString()}` });
  }
  return id;
}

/** The tab's current vault first, then the other vaults it used recently (for looking up real values). */
export function vaultIdsForTab(tabId) {
  const run = queue.then(async () => {
    const cur = await resolveVaultIdNow(tabId, null);
    const store = (await chrome.storage.session.get(SESSION_KEY_VAULT_IDS))[SESSION_KEY_VAULT_IDS] || {};
    return [cur, ...(store[RECENT(tabId)] || []).filter((x) => x !== cur)];
  });
  queue = run.catch(() => {});
  return run;
}
