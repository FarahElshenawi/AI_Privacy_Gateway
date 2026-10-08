// Content script (browser-side file masking) with a minimal fake DOM.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

class FakeDT { constructor() { this.items = { add: (f) => this._f.push(f) }; this._f = []; } get files() { return this._f; } }
class FakeEvent { constructor(type, init = {}) { this.type = type; Object.assign(this, init); this.stopped = false; this.prevented = false; }
  stopImmediatePropagation() { this.stopped = true; } preventDefault() { this.prevented = true; } }
class FakeInput { constructor(files) { this.files = files; this.type = "file"; this.dispatched = []; this.value = "x"; }
  dispatchEvent(e) { this.dispatched.push(e); return true; } }

const notices = [];
globalThis.DataTransfer = FakeDT;
globalThis.DragEvent = class extends FakeEvent { constructor(t, i) { super(t, i); } };
globalThis.ClipboardEvent = class extends FakeEvent { constructor(t, i) { super(t, i); } };
globalThis.Event = FakeEvent;
globalThis.HTMLInputElement = FakeInput;
globalThis.document = {
  body: { appendChild: (el) => notices.push(el.textContent) },
  createElement: () => ({ setAttribute() {}, style: {}, remove() {} }),
  addEventListener() {},
};
let reply;
globalThis.chrome = { runtime: { sendMessage: async (m) => reply(m) } };

// The content script is a classic script (package.json is "type":"module"), so evaluate it
// with a fake CommonJS `module` to get its test hook.
const mod = { exports: {} };
new Function("module", readFileSync(new URL("../src/content/file-interceptor.js", import.meta.url), "utf8"))(mod);
const C = mod.exports;

const file = (name, content) => new File([content], name, { type: "application/pdf" });
const okReply = (m) => ({ success: true, bytesBase64: Buffer.from("MASKED:" + Buffer.from(m.bytesBase64, "base64")).toString("base64"), filename: m.filename, contentType: m.contentType });

test.beforeEach(() => { notices.length = 0; reply = okReply; });

test("picker: original change event is stopped; a synthetic change carries the MASKED file", async () => {
  const input = new FakeInput([file("a.pdf", "secret")]);
  const ev = new FakeEvent("change", { target: input });
  await C.onInputChange(ev);
  assert.ok(ev.stopped && ev.prevented, "page never sees the original event");
  assert.equal(input.dispatched.length, 1);
  assert.equal(await input.files[0].text(), "MASKED:secret");
  assert.ok(C.SYNTHETIC.has(input.dispatched[0]));
});

test("picker: our own synthetic change is not intercepted again", async () => {
  const input = new FakeInput([file("a.pdf", "x")]);
  const ev = new FakeEvent("change", { target: input }); C.SYNTHETIC.add(ev);
  await C.onInputChange(ev);
  assert.equal(ev.stopped, false);
});

test("picker: masking failure → nothing attached (input cleared) and a visible notice", async () => {
  reply = () => ({ success: false, error: "Backend unreachable" });
  const input = new FakeInput([file("a.pdf", "secret")]);
  await C.onInputChange(new FakeEvent("change", { target: input }));
  assert.equal(input.value, "");
  assert.equal(input.dispatched.length, 0);
  assert.match(notices[0], /Backend unreachable/);
});

test("picker: several files → ALL masked; if one fails NONE is attached", async () => {
  const ok = new FakeInput([file("a.pdf", "1"), file("b.pdf", "2")]);
  await C.onInputChange(new FakeEvent("change", { target: ok }));
  assert.equal(ok.files.length, 2);
  assert.equal(await ok.files[1].text(), "MASKED:2");

  reply = (m) => (m.filename === "b.pdf" ? { success: false, error: "unsupported" } : okReply(m));
  const bad = new FakeInput([file("a.pdf", "1"), file("b.pdf", "2")]);
  await C.onInputChange(new FakeEvent("change", { target: bad }));
  assert.equal(bad.dispatched.length, 0);
  assert.equal(bad.value, "");
});

test("drop: files are masked and re-dispatched as a synthetic drop", async () => {
  const dispatched = [];
  const ev = new FakeEvent("drop", { dataTransfer: { files: [file("d.pdf", "secret")] },
    target: { dispatchEvent: (e) => dispatched.push(e) } });
  await C.onDrop(ev);
  assert.ok(ev.stopped && ev.prevented);
  assert.equal(dispatched.length, 1);
  assert.equal(await dispatched[0].dataTransfer.files[0].text(), "MASKED:secret");
});

test("drop without files (text/link drag) is left alone", async () => {
  const ev = new FakeEvent("drop", { dataTransfer: { files: [] }, target: {} });
  await C.onDrop(ev);
  assert.equal(ev.stopped, false);
});

test("paste: pasted files are masked; plain-text paste is untouched; failure shows a notice", async () => {
  const dispatched = [];
  const ev = new FakeEvent("paste", { clipboardData: { files: [file("p.pdf", "secret")] },
    target: { dispatchEvent: (e) => dispatched.push(e) } });
  await C.onPaste(ev);
  assert.equal(await dispatched[0].clipboardData.files[0].text(), "MASKED:secret");

  const text = new FakeEvent("paste", { clipboardData: { files: [] }, target: {} });
  await C.onPaste(text);
  assert.equal(text.stopped, false);

  reply = () => ({ success: false, error: "nope" });
  const bad = new FakeEvent("paste", { clipboardData: { files: [file("p.pdf", "s")] }, target: { dispatchEvent: () => assert.fail("must not dispatch") } });
  await C.onPaste(bad);
  assert.match(notices.at(-1), /nope/);
});

test("protection off → original file passes through unchanged", async () => {
  reply = () => ({ success: true, passthrough: true });
  const f = file("a.pdf", "plain");
  const out = await C.maskOneFile(f);
  assert.equal(out, f);
});
