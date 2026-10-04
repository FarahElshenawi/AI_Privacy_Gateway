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
