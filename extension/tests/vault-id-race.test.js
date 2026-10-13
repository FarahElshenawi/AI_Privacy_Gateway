// A new chat gets its server id while several things ask for its vault at once (the send, the draft
// "prepare", the page's mapping request). They must all get the SAME vault id, or the fakes made for
// the first message are stored under one id and looked up under another (nothing is restored).
import test from "node:test";
import assert from "node:assert/strict";

const slow = (d = {}) => ({                                   // like chrome.storage: every call takes a tick
  get: async (k) => { await new Promise((r) => setTimeout(r, 1)); return k == null ? { ...d } : Object.fromEntries([k].flat().filter((x) => x in d).map((x) => [x, structuredClone(d[x])])); },
  set: async (o) => { await new Promise((r) => setTimeout(r, 1)); Object.assign(d, structuredClone(o)); },
  remove: async () => {},
});
let url = "https://chatgpt.com/";
const ev = () => ({ addListener() {} });
globalThis.chrome = {
  storage: { local: slow(), session: slow(), managed: { get: async () => ({}) }, onChanged: ev() },
  tabs: { get: async () => ({ url }), query: async () => [], onUpdated: ev(), onRemoved: ev(), onActivated: ev() },
  runtime: { id: "x", onMessage: ev(), sendMessage: async () => {} },
  action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
  alarms: { create() {}, onAlarm: ev() },
  debugger: { onEvent: ev(), onDetach: ev() },
};
const { resolveVaultId } = await import("../src/background/vault-id.js");

test("concurrent lookups while a new chat gets its id all return the same vault", async () => {
  const first = await resolveVaultId(5, null);                // new chat: pending id, as on the first send
  assert.match(first, /^new_5_/);
  url = "https://chatgpt.com/c/6acdd6ee-07c4-83ea-8c2d-aaaaaaaaaaaa";   // ChatGPT now shows the conversation id
  const ids = await Promise.all([resolveVaultId(5, null), resolveVaultId(5, null), resolveVaultId(5, "6acdd6ee-07c4-83ea-8c2d-aaaaaaaaaaaa")]);
  assert.deepEqual(ids, [first, first, first]);
  assert.equal(await resolveVaultId(5, null), first, "and stays the same afterwards");
});
