// Page-side demasker: replacement rules, DOM application, and the refresh protocol.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const mod = { exports: {} };
new Function("module", readFileSync(new URL("../src/content/demask.js", import.meta.url), "utf8"))(mod);
const { buildReplacer, applyToTextNode, isEditable, isAssistantTextNode, createDemasker } = mod.exports;

const E = (fake, real) => ({ fake, real });

// ── replacement rules (must match the backend Demasker) ────────────────────────────────
test("replaces a known fake with the real value; text without fakes is unchanged", () => {
  const r = buildReplacer([E("Hager Samir", "Farah Ahmed")]);
  assert.equal(r("Hello Hager Samir, welcome."), "Hello Farah Ahmed, welcome.");
  assert.equal(r("nothing here"), "nothing here");
});

test("every occurrence is replaced", () => {
  const r = buildReplacer([E("Hager", "Farah")]);
  assert.equal(r("Hager met Hager."), "Farah met Farah.");
});

test("longest fake wins over a shorter fake that is its prefix", () => {
  const r = buildReplacer([E("Dana", "Alice"), E("Dana Ruiz", "Alice Brown")]);
  assert.equal(r("Dana Ruiz called Dana."), "Alice Brown called Alice.");
});

test("word boundaries: a fake inside a longer word is NOT replaced", () => {
  const r = buildReplacer([E("James", "Omar")]);
  assert.equal(r("Jamesville and James"), "Jamesville and Omar");
});

test("redaction tokens with brackets restore, and regex metacharacters are safe", () => {
  const r = buildReplacer([E("[[REDACTED:IBAN:de4e]]", "DE89 3704 0044 0532 0130 00"), E("a.b+c@x.io", "real@corp.com")]);
  assert.equal(r("pay to [[REDACTED:IBAN:de4e]] now"), "pay to DE89 3704 0044 0532 0130 00 now");
  assert.equal(r("mail a.b+c@x.io or aXb+c@x.io"), "mail real@corp.com or aXb+c@x.io");
});

test("a real value containing '$&' or '$1' is inserted literally", () => {
  const r = buildReplacer([E("Fakey", "$& and $1")]);
  assert.equal(r("hi Fakey"), "hi $& and $1");
});

test("empty / invalid entries → no replacer", () => {
  assert.equal(buildReplacer([]), null);
  assert.equal(buildReplacer([E("", "x"), { fake: 1, real: "y" }]), null);
  assert.equal(buildReplacer(null), null);
});

// ── DOM application ───────────────────────────────────────────────────────────────────
const textNode = (value, parent = null) => ({ nodeType: 3, nodeValue: value, parentElement: parent, parentNode: parent });
const el = (editable, assistant = false) => ({
  closest: (selector) => {
    if (selector.includes("[data-message-author-role") || selector.includes("model-response")) {
      return assistant ? {} : null;
    }
    return editable ? {} : null;
  }
});

test("applyToTextNode rewrites in place and does not loop on its own output", () => {
  const r = buildReplacer([E("Hager", "Farah")]);
  const written = new WeakMap();
  const n = textNode("hi Hager");
  assert.equal(applyToTextNode(n, r, written), true);
  assert.equal(n.nodeValue, "hi Farah");
  assert.equal(applyToTextNode(n, r, written), false);
});

test("user-authored message text is never demasked", async () => {
  const n = textNode("Hager", el(false, false));
  const d = createDemasker(fakeDoc([n]), async () => ({
    success: true, enabled: true, changed: true, version: 1,
    entries: [E("Hager", "Farah")]
  }));
  await d.refresh();
  assert.equal(n.nodeValue, "Hager");
  assert.equal(isAssistantTextNode(n), false);
});

test("assistant message text is eligible for demasking", () => {
  const n = textNode("Hager", el(false, true));
  assert.equal(isAssistantTextNode(n), true);
});

test("editable regions (the composer) are never rewritten", () => {
  assert.equal(isEditable(textNode("x", el(true))), true);
  assert.equal(isEditable(textNode("x", el(false))), false);
});

// ── refresh protocol ──────────────────────────────────────────────────────────────────
function fakeDoc(nodes) {
  const listeners = {};
  return {
    hidden: false, documentElement: { nodeType: 1 }, body: { nodeType: 1 },
    createTreeWalker: () => { let i = 0; return { nextNode: () => nodes[i++] || null }; },
    addEventListener: (t, f) => { listeners[t] = f; },
  };
}

test("refresh: first call gets entries and restores text already on screen", async () => {
  const n = textNode("Dear Hager Samir", el(false, true));
  const sent = [];
  const d = createDemasker(fakeDoc([n]), async (m) => { sent.push(m); return { success: true, enabled: true, changed: true, version: 3, entries: [E("Hager Samir", "Farah Ahmed")] }; });
  await d.refresh();
  assert.equal(n.nodeValue, "Dear Farah Ahmed");
  assert.deepEqual(sent[0], { type: "GET_MAPPING", sinceVersion: null });
  await d.refresh();
  assert.equal(sent[1].sinceVersion, 3, "sends the version it already holds");
});

test("refresh: unchanged → keeps the current replacer; disabled → stops replacing", async () => {
  let reply = { success: true, enabled: true, changed: true, version: 1, entries: [E("Hager", "Farah")] };
  const d = createDemasker(fakeDoc([]), async () => reply);
  await d.refresh();
  reply = { success: true, enabled: true, changed: false, version: 1 };
  await d.refresh();
  assert.ok(d._state().replacer);
  reply = { success: true, enabled: false };
  await d.refresh();
  assert.equal(d._state().replacer, null);
});

test("refresh: errors (worker asleep, backend down) keep what is already known", async () => {
  let fail = false;
  const d = createDemasker(fakeDoc([]), async () => {
    if (fail) throw new Error("Extension context invalidated");
    return { success: true, enabled: true, changed: true, version: 1, entries: [E("Hager", "Farah")] };
  });
  await d.refresh();
  fail = true;
  await d.refresh();
  assert.ok(d._state().replacer);
  const bad = createDemasker(fakeDoc([]), async () => ({ success: false, error: "Local backend unreachable" }));
  await bad.refresh();
  assert.equal(bad._state().replacer, null);
});

test("reset (new conversation) drops the old mapping before asking for the new one", async () => {
  let call = 0;
  const d = createDemasker(fakeDoc([]), async (m) => {
    call++;
    return call === 1
      ? { success: true, enabled: true, changed: true, version: 5, entries: [E("A", "x")] }
      : { success: true, enabled: true, changed: true, version: 1, entries: [] };
  });
  await d.refresh();
  d.reset();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(d._state().replacer, null);
  assert.equal(d._state().version, 1);
});
