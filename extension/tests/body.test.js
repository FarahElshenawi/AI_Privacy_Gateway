import test from "node:test";
import assert from "node:assert/strict";
import {
  collectUserTextSlots, collectAttachmentNameSlots, countResidualOriginals,
  bytesToBase64, base64ToBytes, concatBytes, sniffExtension, splitFilename,
} from "../src/background/body.js";

const body = () => ({
  conversation_id: null,
  messages: [
    { author: { role: "system" }, content: { parts: ["secret system Farah"] } },
    { author: { role: "user" }, content: { parts: ["Hi I am Farah", { text: "Farah again" }, { image: 1 }] },
      metadata: { attachments: [{ name: "Farah_CV.pdf" }] } },
    { role: "user", content: { text: "legacy Farah" } },
  ],
});

test("collects only user text slots, per part", () => {
  const p = body();
  const slots = collectUserTextSlots(p);
  assert.equal(slots.length, 3);
  slots.forEach((s) => s.set(s.get().replaceAll("Farah", "Hager")));
  assert.equal(p.messages[0].content.parts[0], "secret system Farah");
  assert.equal(p.messages[1].content.parts[0], "Hi I am Hager");
  assert.equal(p.messages[1].content.parts[1].text, "Hager again");
  assert.equal(p.messages[2].content.text, "legacy Hager");
});

test("set uses assignment: '$&' in replacement is literal", () => {
  const p = body();
  const [s] = collectUserTextSlots(p);
  s.set("cost $& $1");
  assert.equal(p.messages[1].content.parts[0], "cost $& $1");
});

test("attachment names are collected", () => {
  const [s] = collectAttachmentNameSlots(body());
  assert.equal(s.get(), "Farah_CV.pdf");
});

test("residual check counts originals only", () => {
  assert.equal(countResidualOriginals("hello Hager", [["Farah", "Hager"]]), 0);
  assert.equal(countResidualOriginals("hello Farah", [["Farah", "Hager"]]), 1);
  assert.equal(countResidualOriginals("Farah", [["Farah", "Farah Q"]]), 0);
});

test("base64 roundtrip is binary safe", () => {
  const b = new Uint8Array(70000).map((_, i) => i % 256);
  assert.deepEqual(base64ToBytes(bytesToBase64(b)), b);
  assert.deepEqual(concatBytes([b.subarray(0, 3), b.subarray(3)]), b);
});

test("filename helpers", () => {
  assert.equal(sniffExtension(new TextEncoder().encode("%PDF-1.7"), ""), ".pdf");
  assert.equal(sniffExtension(new Uint8Array(4), "text/csv; charset=x"), ".csv");
  assert.deepEqual(splitFilename("a.b.docx"), { stem: "a.b", ext: ".docx" });
  assert.deepEqual(splitFilename(".env"), { stem: ".env", ext: "" });
});

import {
  parseGeminiBody, collectGeminiSlots, buildGeminiBody, geminiConversationId,
  parseMultipart, buildMultipart, boundaryFromContentType,
} from "../src/background/body.js";

test("gemini f.req roundtrip: prompt and file names are slots, other fields preserved", () => {
  const inner = [["hi I am Farah", 0, null, [[["/contrib/x"], "Farah_CV.pdf"]], null, null, 0], ["en"], ["c_123", "r_1", "rc_1"], null, 5.5];
  const body = new URLSearchParams({ "f.req": JSON.stringify([null, JSON.stringify(inner)]), at: "TOKEN:123" }).toString() + "&";
  const st = parseGeminiBody(new TextEncoder().encode(body));
  assert.equal(geminiConversationId(st), "c_123");
  const { text, names } = collectGeminiSlots(st);
  assert.equal(text[0].get(), "hi I am Farah");
  assert.equal(names[0].get(), "Farah_CV.pdf");
  text[0].set("hi I am Hager"); names[0].set("Hager_CV.pdf");
  const out = new URLSearchParams(new TextDecoder().decode(buildGeminiBody(st)));
  assert.equal(out.get("at"), "TOKEN:123");
  const back = JSON.parse(JSON.parse(out.get("f.req"))[1]);
  assert.equal(back[0][0], "hi I am Hager");
  assert.equal(back[0][3][0][1], "Hager_CV.pdf");
  assert.deepEqual(back[1], ["en"]);
  assert.equal(back[0][3][0][0][0], "/contrib/x");
});

test("gemini parse fails closed on unknown shapes", () => {
  assert.throws(() => parseGeminiBody(new TextEncoder().encode("x=1")));
  assert.throws(() => parseGeminiBody(new TextEncoder().encode("f.req=" + encodeURIComponent('{"a":1}'))));
});

test("multipart roundtrip is binary safe and lets us swap file bytes + filename", () => {
  const bin = Uint8Array.from([0, 255, 13, 10, 45, 45, 1, 2, 3, 13, 10]); // includes CRLF and '--'
  const b = "----WebKitFormBoundaryX";
  const mk = (bytes) => buildMultipart([
    { headers: [["Content-Disposition", 'form-data; name="meta"']], bytes: new TextEncoder().encode("hello"), name: "meta", filename: null },
    { headers: [["Content-Disposition", 'form-data; name="file"; filename="Farah.pdf"'], ["Content-Type", "application/pdf"]], bytes, name: "file", filename: "Farah.pdf", contentType: "application/pdf" },
  ], b);
  assert.equal(boundaryFromContentType(`multipart/form-data; boundary=${b}`), b);
  const parts = parseMultipart(mk(bin), b);
  assert.equal(parts.length, 2);
  assert.deepEqual(parts[1].bytes, bin);
  assert.equal(parts[1].filename, "Farah.pdf");
  parts[1].bytes = new Uint8Array([9, 9]); parts[1].filename = "Hager.pdf";
  const again = parseMultipart(buildMultipart(parts, b), b);
  assert.deepEqual(again[1].bytes, new Uint8Array([9, 9]));
  assert.equal(again[1].filename, "Hager.pdf");
  assert.equal(new TextDecoder().decode(again[0].bytes), "hello");
});
