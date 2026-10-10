// The REAL service worker: restart recovery, admin lock, re-attach after a detach.
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

const b64 = (u8) => Buffer.from(u8).toString("base64");
let onEvent, onMessage, onDetach, calls = [], attaches = [], managed = {};
const mem = (d = {}) => ({
  get: async (k) => (k == null ? { ...d } : Object.fromEntries([k].flat().filter((x) => x in d).map((x) => [x, d[x]]))),
  set: async (o) => { Object.assign(d, o); },
  remove: async (k) => { delete d[k]; },
});
const ev = () => ({ addListener() {} });
const BODY = Uint8Array.from([0x25, 0x50, 0x44, 0x46, 1, 2, 3]);
const hash = createHash("sha256").update(BODY).digest("hex");
let tabUrl = "https://chatgpt.com/c/x";

const local = mem();
globalThis.chrome = {
  storage: {
    local,
    // a worker that was restarted: the previous instance had authorized this exact masked file
    session: mem({ doppel_state: { authorizedMaskedFiles: [[7, [{ hash, expiresAt: Date.now() + 60000, filename: "f.pdf" }]]],
                                   pendingVaultId: [[7, "new_7_keep"]] } }),
    managed: { get: async (k) => { if (managed === null) throw new Error("no policy"); return { ...managed }; } },
    onChanged: ev(),
  },
  tabs: { query: async () => [], onUpdated: ev(), onRemoved: ev(), onActivated: ev(), get: async () => ({ url: tabUrl }) },
  action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
  alarms: { create() {}, onAlarm: ev() },
  runtime: { id: "x", onMessage: { addListener: (fn) => { onMessage = fn; } }, onStartup: ev(), onInstalled: ev(), sendMessage: async () => {} },
  debugger: {
    onEvent: { addListener: (fn) => { onEvent = fn; } },
    onDetach: { addListener: (fn) => { onDetach = fn; } },
    attach: async (t) => { attaches.push(t.tabId); }, detach: async () => {}, getTargets: async () => [],
    sendCommand: async (_s, method, p) => { calls.push({ method, p }); return {}; },
  },
};
globalThis.fetch = async () => { throw new Error("backend must not be called"); };
await import("../src/background/service-worker.js");

const AZ = "https://abc.blob.core.windows.net/files/x?sig=1";
const sendMsg = (message, sender) => new Promise((resolve) => onMessage(message, sender, resolve));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test("a restarted worker still honours the authorization saved by the previous one (no second masking)", async () => {
  calls = [];
  await onEvent({ tabId: 7 }, "Fetch.requestPaused", { requestId: "r1",
    request: { url: AZ, method: "PUT", hasPostData: true, headers: { "content-type": "application/pdf" },
               postDataEntries: [{ bytes: b64(BODY) }] } });
  const c = calls.find((x) => x.method === "Fetch.continueRequest");
  assert.ok(c && c.p.postData === undefined, "forwarded unchanged on the saved authorization");
  assert.ok(!calls.some((x) => x.method === "Fetch.failRequest"));
});

test("admin lock: user switch off is overridden, files are not waved through", async () => {
  await chrome.storage.local.set({ protectionEnabled: false });
  managed = {};
  const off = await sendMsg({ type: "MASK_SELECTED_FILE", filename: "a.pdf", contentType: "application/pdf", bytesBase64: b64(BODY) },
    { tab: { id: 7, url: tabUrl } });
  assert.equal(off.passthrough, true, "no lock: the user's own switch applies");
  managed = { protectionLocked: true };
  const on = await sendMsg({ type: "MASK_SELECTED_FILE", filename: "a.pdf", contentType: "application/pdf", bytesBase64: b64(BODY) },
    { tab: { id: 7, url: tabUrl } });
  assert.notEqual(on.passthrough, true, "locked: protection stays on");
  managed = null;                                           // unreadable policy = not locked
  const unreadable = await sendMsg({ type: "MASK_SELECTED_FILE", filename: "a.pdf", contentType: "application/pdf", bytesBase64: b64(BODY) },
    { tab: { id: 7, url: tabUrl } });
  assert.equal(unreadable.passthrough, true);
  managed = {};
  await chrome.storage.local.set({ protectionEnabled: true });
});

test("a detach by Chrome/DevTools is re-attached immediately; the user's own Cancel is respected", async () => {
  attaches.length = 0;
  onDetach({ tabId: 9 }, "replaced_with_devtools");
  await sleep(50);
  assert.ok(attaches.includes(9), "re-attached");
  attaches.length = 0;
  onDetach({ tabId: 10 }, "canceled_by_user");
  await sleep(50);
  assert.ok(!attaches.includes(10), "user's Cancel respected");
  managed = { protectionLocked: true };
  onDetach({ tabId: 11 }, "canceled_by_user");
  await sleep(50);
  assert.ok(attaches.includes(11), "an admin lock overrides Cancel");
  managed = {};
  attaches.length = 0;
  onDetach({ tabId: 12 }, "target_closed");
  await sleep(50);
  assert.ok(!attaches.includes(12));
});

test("a message from a chat tab with no debugger triggers a re-attach", async () => {
  attaches.length = 0;
  await sendMsg({ type: "PING" }, { tab: { id: 21, url: tabUrl } });
  await sleep(50);
  assert.ok(attaches.includes(21));
});
