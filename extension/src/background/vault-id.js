import { convIdFromUrl } from "./body.js";
import { SESSION_KEY_VAULT_IDS, pendingVaultId } from "./core.js";

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
