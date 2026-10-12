import { convIdFromUrl } from "./body.js";
import { SESSION_KEY_VAULT_IDS, pendingVaultId } from "./core.js";

// ─────────────────────────────────────────────────────────
// Vault id: stable per conversation (shared by prompts AND files)
// ─────────────────────────────────────────────────────────

export async function tabConvId(tabId) {
  try { return convIdFromUrl((await chrome.tabs.get(tabId)).url); } catch { return null; }
}

export async function resolveVaultId(tabId, serverConvId) {
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
