import { convIdFromUrl } from "./body.js";
import { SESSION_KEY_VAULT_IDS, lastVaultId, pendingVaultId } from "./core.js";

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

async function resolveVaultIdNow(tabId, serverConvId) {
  // File uploads carry no conversation id in their bodies; the tab URL does
  // (/c/<id>, /app/<id>). Without this, a file uploaded into an existing chat
  // got a different vault than the prompts → inconsistent surrogates.
  serverConvId = serverConvId || await tabConvId(tabId);
  const store = (await chrome.storage.session.get(SESSION_KEY_VAULT_IDS))[SESSION_KEY_VAULT_IDS] || {};
  let id;
  if (serverConvId) {
    id = store[serverConvId];
    if (!id) {
      // Fall back to pending, then to lastVaultId (the last vault this tab used for masking).
      // Without lastVaultId, a race between handleSend and handleGetMapping could create
      // a brand-new conv_<id> vault that has no entries, while the real entries sit under
      // the pending id that was already deleted.
      id = pendingVaultId.get(tabId) || lastVaultId.get(tabId) || `conv_${serverConvId}`;
      pendingVaultId.delete(tabId);
      store[serverConvId] = id;
      await chrome.storage.session.set({ [SESSION_KEY_VAULT_IDS]: store });
    }
  } else {
    id = pendingVaultId.get(tabId) || lastVaultId.get(tabId);
    if (!id) {
      id = `new_${tabId}_${crypto.randomUUID()}`;
      pendingVaultId.set(tabId, id);
    }
  }
  // Always remember the last vault ID used for this tab, so a later lookup (e.g. GET_MAPPING
  // after the SW restarted or after pendingVaultId was deleted) can fall back to it instead
  // of inventing a new, empty vault.
  lastVaultId.set(tabId, id);
  console.log(`[Doppel vault] tab=${tabId} serverConvId=${serverConvId || "null"} → vault=${id}`);
  return id;
}
