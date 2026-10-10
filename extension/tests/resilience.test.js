// Service-worker restarts, admin lock, and re-attach after a detach.
import test from "node:test";
import assert from "node:assert/strict";
import { createState } from "../src/background/state.js";

const mem = (d = {}) => ({
  get: async (k) => (k == null ? { ...d } : Object.fromEntries([k].flat().filter((x) => x in d).map((x) => [x, d[x]]))),
  set: async (o) => { Object.assign(d, o); },
  remove: async (k) => { delete d[k]; },
  _d: d,
});

test("state survives a restart; file bytes are never persisted", async () => {
  const area = mem();
  const a = createState(() => area);
  const files = a.map("files", { strip: (q) => q.map(({ hash, expiresAt }) => ({ hash, expiresAt })) });
  const ids = a.map("ids");
  files.set(7, [{ hash: "h1", expiresAt: 9e15, bytes: new Uint8Array([1, 2, 3]) }]);
  ids.set(7, "new_7_abc");
  await new Promise((r) => setTimeout(r, 0));
  const raw = JSON.stringify(area._d);
  assert.ok(!raw.includes('"bytes"') && !raw.includes("[1,2,3]"));

  const b = createState(() => area);          // the restarted worker
  const files2 = b.map("files");
  const ids2 = b.map("ids");
  await b.hydrate();
  assert.deepEqual(files2.get(7), [{ hash: "h1", expiresAt: 9e15 }]);
  assert.equal(ids2.get(7), "new_7_abc");
});

test("in-place queue mutation after get() is captured", async () => {
  const area = mem();
  const s = createState(() => area);
  const m = s.map("q");
  m.set(1, ["a"]);
  await new Promise((r) => setTimeout(r, 0));
  m.get(1).push("b");
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(area._d.doppel_state.q, [[1, ["a", "b"]]]);
});

test("entries created before hydration win over stale saved ones", async () => {
  const area = mem({ doppel_state: { ids: [[7, "old"], [8, "other"]] } });
  const s = createState(() => area);
  const ids = s.map("ids");
  ids.set(7, "fresh");
  await s.hydrate();
  assert.equal(ids.get(7), "fresh");
  assert.equal(ids.get(8), "other");
});

test("a corrupt saved snapshot is ignored", async () => {
  const s = createState(() => mem({ doppel_state: "garbage" }));
  s.map("ids");
  await s.hydrate();
});
