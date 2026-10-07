import test from "node:test";
import assert from "node:assert/strict";
import {
  collectUserTextSlots, collectAttachmentNameSlots, collectPrepareSlots, countResidualOriginals,
  bytesToBase64, base64ToBytes, concatBytes, sniffExtension, splitFilename, convIdFromUrl,
  parseGeminiBody, collectGeminiSlots, buildGeminiBody, geminiConversationId,
  parseGeminiStartName, buildGeminiStartBody,
  parseMultipart, buildMultipart, boundaryFromContentType,
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

test("prepare: partial_query text is a slot", () => {
  const p = { conversation_id: null, partial_query: { id: "x", author: { role: "user" }, content: { content_type: "text", parts: ["my SSN is Farah"] } } };
  const slots = collectPrepareSlots(p);
  assert.equal(slots.length, 1);
  slots[0].set("masked");
  assert.equal(p.partial_query.content.parts[0], "masked");
  assert.equal(collectPrepareSlots({}).length, 0);
  assert.equal(collectPrepareSlots(null).length, 0);
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
  assert.equal(sniffExtension(new Uint8Array(4), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), ".docx");
  assert.equal(sniffExtension(new Uint8Array(4), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), ".xlsx");
  assert.deepEqual(splitFilename("a.b.docx"), { stem: "a.b", ext: ".docx" });
  assert.deepEqual(splitFilename(".env"), { stem: ".env", ext: "" });
});

test("convIdFromUrl: ChatGPT and Gemini", () => {
  assert.equal(convIdFromUrl("https://chatgpt.com/c/68f1a2b3-1111-2222-3333-444455556666"), "68f1a2b3-1111-2222-3333-444455556666");
  assert.equal(convIdFromUrl("https://chatgpt.com/g/g-abc/c/68f1a2b3-1111-2222-3333-444455556666?x=1"), "68f1a2b3-1111-2222-3333-444455556666");
  assert.equal(convIdFromUrl("https://chatgpt.com/"), null);
  assert.equal(convIdFromUrl("https://gemini.google.com/app/abc123def"), "c_abc123def");
  assert.equal(convIdFromUrl("https://gemini.google.com/u/1/app/abc123def"), "c_abc123def");
  assert.equal(convIdFromUrl("https://gemini.google.com/app"), null);
  assert.equal(convIdFromUrl("not a url"), null);
});

test("gemini f.req roundtrip: prompt and file names are slots, other fields preserved", () => {
  const inner = [["hi I am Farah", 0, null, [[["/contrib/x"], "Farah_CV.pdf"]], null, null, 0], ["en"], ["c_123", "r_1", "rc_1"], null, 5.5];
  const b = new URLSearchParams({ "f.req": JSON.stringify([null, JSON.stringify(inner)]), at: "TOKEN:123" }).toString() + "&";
  const st = parseGeminiBody(new TextEncoder().encode(b));
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

test("gemini resumable start body: filename parsed and rebuilt", () => {
  const start = new TextEncoder().encode("File name=Farah+CV%281%29.pdf");
  assert.equal(parseGeminiStartName(start), "Farah CV(1).pdf");
  assert.equal(parseGeminiStartName(new TextEncoder().encode("")), null);
  assert.equal(parseGeminiStartName(new TextEncoder().encode("{json:true}")), null);
  const rebuilt = buildGeminiStartBody("Hager CV(1).pdf");
  assert.equal(parseGeminiStartName(rebuilt), "Hager CV(1).pdf");
});

test("multipart roundtrip is binary safe and lets us swap file bytes + filename", () => {
  const bin = Uint8Array.from([0, 255, 13, 10, 45, 45, 1, 2, 3, 13, 10]);
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

import { sniffBinaryExtension, fixFilename, describeBackendError } from "../src/background/body.js";

const zipOf = (marker, size = 400) => { const b = new Uint8Array(size); b.set([0x50, 0x4b, 3, 4]); b.set(new TextEncoder().encode(marker), 100); return b; };

test("sniffBinaryExtension: pdf / docx / xlsx from bytes, nothing for other zips", () => {
  assert.equal(sniffBinaryExtension(new TextEncoder().encode("%PDF-1.4")), ".pdf");
  assert.equal(sniffBinaryExtension(zipOf("word/document.xml")), ".docx");
  assert.equal(sniffBinaryExtension(zipOf("xl/workbook.xml")), ".xlsx");
  assert.equal(sniffBinaryExtension(zipOf("ppt/presentation.xml")), "");
  assert.equal(sniffBinaryExtension(new TextEncoder().encode("plain text")), "");
});

test("sniffBinaryExtension: finds names in the central directory of a large file (tail window)", () => {
  const big = new Uint8Array(3 * 1024 * 1024); big.set([0x50, 0x4b, 3, 4]);
  big.set(new TextEncoder().encode("xl/workbook.xml"), big.length - 500);
  assert.equal(sniffBinaryExtension(big), ".xlsx");
});

test("fixFilename: content wins over a missing/wrong extension; stem is never changed", () => {
  assert.equal(fixFilename("report", zipOf("word/document.xml"), ""), "report.docx");
  assert.equal(fixFilename("Hager_CV.bin", zipOf("word/document.xml"), ""), "Hager_CV.docx");
  assert.equal(fixFilename("Book.XLSX", zipOf("xl/workbook.xml"), ""), "Book.XLSX");
  assert.equal(fixFilename("notes.txt", new TextEncoder().encode("hi"), "text/plain"), "notes.txt");
  assert.equal(fixFilename("", new TextEncoder().encode("%PDF-1.7"), ""), "uploaded_file.pdf");
  assert.equal(fixFilename("data", new TextEncoder().encode("a,b"), "text/csv"), "data.csv");
});

test("describeBackendError: readable, content-free, only machine codes echoed", () => {
  assert.match(describeBackendError(400, { error: "image_files_need_ocr_not_supported", blockers: ["image_file"] }, "file"), /Images can't be masked/);
  assert.match(describeBackendError(422, { error: "blocked", blockers: ["tracked_changes", "comments"] }, "file"), /blockers: tracked_changes, comments/);
  assert.match(describeBackendError(422, { error: "residual_leak", leak_types: ["EMAIL"] }, "file"), /still contained/);
  assert.match(describeBackendError(413, null, "file"), /50 MB/);
  assert.match(describeBackendError(413, null, "text"), /200,000/);
  assert.match(describeBackendError(401, null), /token/);
  assert.match(describeBackendError(503, null), /engine failed/);
  // free text / non-code values from a misbehaving backend are NOT echoed
  const r = describeBackendError(422, { error: "John Smith's salary is 5000", blockers: ["John Smith"], leak_types: ["a b c"] }, "file");
  assert.ok(!/John|salary|5000/.test(r), r);
});
