// End-to-end test of the REAL service-worker.js against a simulated CDP session.
// chrome.* and the local backend are mocked; every upload path must either be
// masked (continueRequest with the masked body) or blocked (failRequest).
import test from "node:test";
import assert from "node:assert/strict";

const b64 = (u8) => Buffer.from(u8).toString("base64");
const enc = (s) => new TextEncoder().encode(s);

let onEvent, onMessage, calls, store, backendUp = true, postDataResult;

function resetCalls() { calls = []; postDataResult = null; backendUp = true; maskOverride = null; fileHeaders = {}; lastFile = null; }

const mem = () => {
  const d = {};
  return {
    get: async (k) => {
      if (k == null) return { ...d };
      const keys = Array.isArray(k) ? k : [k];
      return Object.fromEntries(keys.filter((x) => x in d).map((x) => [x, d[x]]));
    },
    set: async (o) => { Object.assign(d, o); },
    remove: async (k) => { delete d[k]; },
  };
};
const ev = () => ({ addListener() {} });

globalThis.chrome = {
  storage: { local: mem(), session: mem(), onChanged: ev() },
  tabs: {
    query: async () => [], onUpdated: ev(), onRemoved: ev(), onActivated: ev(),
    get: async () => ({ url: "https://chatgpt.com/" }),
  },
  action: { setBadgeText() {}, setBadgeBackgroundColor() {} },
  alarms: { create() {}, onAlarm: ev() },
  runtime: { id: "x", onMessage: { addListener: (fn) => { onMessage = fn; } }, onStartup: ev(), onInstalled: ev(), sendMessage: async () => {} },
  debugger: {
    onEvent: { addListener: (fn) => { onEvent = fn; } },
    onDetach: ev(),
    attach: async () => {}, detach: async () => {}, getTargets: async () => [],
    sendCommand: async (_s, method, p) => {
      calls.push({ method, p });
      if (method === "Network.getRequestPostData") {
        if (postDataResult instanceof Error) throw postDataResult;
        return postDataResult;
      }
      return {};
    },
  },
};

// Fake backend that reproduces the REAL contract of local-backend/app/api/*.py:
//  - /token (loopback) → {token}; /api/* require "Authorization: Bearer <token>" else 401
//  - /api/mask → MaskResponse (NO `pairs` field), 413 over the char limit
//  - /api/process_file → masked bytes + X-DLP-* headers; on refusal 422/400 with
//    {detail:{error,blockers,warnings,leak_types}} ; type is decided from bytes + EXTENSION
let serverToken = "t1";
let maskOverride = null;     // partial MaskResponse overrides for one test
let fileHeaders = {};        // extra X-DLP-* headers for one test
let lastFile = null;         // { filename, size } the backend received
let tokenFetches = 0;
let mappingCalls = [];
let mappingDown = false;
const jsonErr = (status, detail) => new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json" } });

globalThis.fetch = async (url, opts) => {
  if (!backendUp) throw new Error("connect ECONNREFUSED");
  const u = String(url);
  if (u.endsWith("/token")) { tokenFetches++; return Response.json({ token: serverToken }); }
  const auth = (opts.headers || {}).Authorization;
  if (u.includes("/api/") && auth !== `Bearer ${serverToken}`) return jsonErr(401, "Invalid or missing token");

  if (u.endsWith("/api/mask")) {
    const { text } = JSON.parse(opts.body);
    if (text.length > 200000) return jsonErr(413, "Text longer than 200000 characters");
    const hit = text.includes("Farah");
    return Response.json({
      masked_text: text.replaceAll("Farah", "Hager"),
      entities_found: hit ? 1 : 0, entity_types: hit ? ["PERSON"] : [], leaks: [],
      safe_to_send: true, degraded: false, blocked: false, coverage_complete: true,
      uncovered_labels: [], degraded_reasons: [], strict: false,
      ...(maskOverride || {}),
    });
  }
  if (u.endsWith("/api/mapping")) {
    const { conversation_id, since_version } = JSON.parse(opts.body);
    mappingCalls.push({ conversation_id, since_version });
    if (mappingDown) return jsonErr(503, "down");
    return Response.json(since_version === 4
      ? { version: 4, changed: false, entries: [] }
      : { version: 4, changed: true, entries: [{ fake: "Hager", real: "Farah" }] });
  }
  if (u.endsWith("/api/process_file")) {
    const f = opts.body.get("file");
    const bytes = new Uint8Array(await f.arrayBuffer());
    const name = f.name || "";
    lastFile = { filename: name, size: bytes.length };
    const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")).toLowerCase() : "";
    const head = Buffer.from(bytes.subarray(0, 8));
    const isPdf = head.subarray(0, 4).toString() === "%PDF";
    const isZip = head[0] === 0x50 && head[1] === 0x4b;
    const isPng = head[0] === 0x89 && head.subarray(1, 4).toString() === "PNG";
    if (isPng) return jsonErr(400, { error: "image_files_need_ocr_not_supported", blockers: ["image_file"], warnings: [], leak_types: [] });
    if (!isPdf && !(isZip && (ext === ".docx" || ext === ".xlsx")) && ![".txt", ".csv"].includes(ext) && !(ext === "" && !isZip && head.every((b) => b < 128 && b !== 0))) {
      return jsonErr(400, { error: "unknown_file_type", blockers: [], warnings: [], leak_types: [] });
    }
    return new Response(Buffer.concat([Buffer.from("MASKED:"), Buffer.from(bytes)]), {
      headers: { "X-DLP-Degraded": "false", "X-DLP-Uncovered-Labels": "", "X-DLP-Warnings": "", "X-DLP-Replacements": "3", ...fileHeaders },
    });
  }
  return new Response("nope", { status: 404 });
};

await import("../src/background/service-worker.js");

const SRC = { tabId: 7 };
const fire = (params) => onEvent(SRC, "Fetch.requestPaused", params);
const outcome = () => {
  const c = calls.find((x) => x.method === "Fetch.continueRequest");
  const f = calls.find((x) => x.method === "Fetch.failRequest");
  return { cont: c && c.p, failed: !!f };
};
const sentBody = (o) => Buffer.from(o.cont.postData, "base64");

const AZ = "https://abc.blob.core.windows.net/files/x?sig=1";
const PDF = Uint8Array.from([0x25, 0x50, 0x44, 0x46, 0xff, 0x00, 0xfe, 13, 10]); // binary, not UTF-8

test("setup", () => { assert.equal(typeof onEvent, "function"); });

test("ChatGPT file PUT, blob-backed body (entries WITHOUT bytes) → fetched via getRequestPostData and masked", async () => {
  resetCalls();
  postDataResult = { postData: b64(PDF), base64Encoded: true };
  await fire({
    requestId: "r1", networkId: "n1",
    request: { url: AZ, method: "PUT", hasPostData: true, headers: { "Content-Type": "application/pdf" },
               postDataEntries: [{}] },
  });
  const o = outcome();
  assert.ok(o.cont && !o.failed, "must continue with a rewritten body");
  assert.deepEqual([...sentBody(o).subarray(0, 7)], [...Buffer.from("MASKED:")]);
  assert.deepEqual([...sentBody(o).subarray(7)], [...PDF], "binary bytes preserved");
});

test("ChatGPT file PUT with inline base64 entries → masked", async () => {
  resetCalls();
  await fire({
    requestId: "r2",
    request: { url: AZ, method: "PUT", hasPostData: true, headers: { "content-type": "application/pdf" },
               postDataEntries: [{ bytes: b64(PDF) }] },
  });
  const o = outcome();
  assert.ok(o.cont && !o.failed);
  assert.equal(sentBody(o).subarray(0, 7).toString(), "MASKED:");
});

test("file PUT whose bytes cannot be retrieved → BLOCKED, never continued", async () => {
  resetCalls();
  postDataResult = new Error("No post data available for the request");
  await fire({
    requestId: "r3", networkId: "n3",
    request: { url: AZ, method: "PUT", hasPostData: true, headers: {}, postDataEntries: [{}] },
  });
  const o = outcome();
  assert.ok(o.failed && !o.cont);
});

test("lossy string postData for a binary file is NOT trusted → BLOCKED", async () => {
  resetCalls();
  postDataResult = { postData: "%PDF��", base64Encoded: false };
  await fire({
    requestId: "r4", networkId: "n4",
    request: { url: AZ, method: "PUT", hasPostData: true, headers: {}, postData: "%PDF��" },
  });
  const o = outcome();
  assert.ok(o.failed && !o.cont);
});

test("backend down → file upload BLOCKED (fail closed)", async () => {
  resetCalls(); backendUp = false;
  await fire({
    requestId: "r5",
    request: { url: AZ, method: "PUT", hasPostData: true, headers: {}, postDataEntries: [{ bytes: b64(PDF) }] },
  });
  const o = outcome();
  assert.ok(o.failed && !o.cont);
});

test("Azure chunked block upload (comp=block) → BLOCKED", async () => {
  resetCalls();
  await fire({
    requestId: "r6",
    request: { url: "https://abc.blob.core.windows.net/f?comp=block&blockid=AA", method: "PUT",
               hasPostData: true, headers: {}, postDataEntries: [{ bytes: b64(PDF) }] },
  });
  assert.ok(outcome().failed);
});

test("Azure comp=blocklist (metadata only) passes through", async () => {
  resetCalls();
  await fire({
    requestId: "r7",
    request: { url: "https://abc.blob.core.windows.net/f?comp=blocklist", method: "PUT",
               hasPostData: true, headers: {}, postDataEntries: [{ bytes: b64(enc("<xml/>")) }] },
  });
  const o = outcome();
  assert.ok(o.cont && !o.cont.postData && !o.failed);
});

test("ChatGPT reserve masks file_name; next PUT is named from the queue", async () => {
  resetCalls();
  await fire({
    requestId: "r8",
    request: { url: "https://chatgpt.com/backend-api/files", method: "POST", hasPostData: true,
               headers: {}, postDataEntries: [{ bytes: b64(enc(JSON.stringify({ file_name: "Farah_CV.pdf", file_size: 5 }))) }] },
  });
  const o = outcome();
  assert.equal(JSON.parse(sentBody(o).toString()).file_name, "Hager_CV.pdf");
});

test("ChatGPT text send is masked", async () => {
  resetCalls();
  const payload = { conversation_id: null, messages: [{ author: { role: "user" }, content: { parts: ["I am Farah"] } }] };
  await fire({
    requestId: "r9",
    request: { url: "https://chatgpt.com/backend-api/f/conversation", method: "POST", hasPostData: true,
               headers: {}, postDataEntries: [{ bytes: b64(enc(JSON.stringify(payload))) }] },
  });
  const o = outcome();
  assert.equal(JSON.parse(sentBody(o).toString()).messages[0].content.parts[0], "I am Hager");
});

test("ChatGPT /prepare draft text is masked", async () => {
  resetCalls();
  const payload = { conversation_id: null, partial_query: { author: { role: "user" }, content: { parts: ["I am Farah"] } } };
  await fire({
    requestId: "r10",
    request: { url: "https://chatgpt.com/backend-api/f/conversation/prepare", method: "POST", hasPostData: true,
               headers: {}, postDataEntries: [{ bytes: b64(enc(JSON.stringify(payload))) }] },
  });
  const o = outcome();
  assert.equal(JSON.parse(sentBody(o).toString()).partial_query.content.parts[0], "I am Hager");
});

test("Gemini resumable: start masks filename, finalize masks raw bytes", async () => {
  resetCalls();
  const base = "https://push.clients6.google.com/upload/";
  await fire({
    requestId: "g1",
    request: { url: base, method: "POST", hasPostData: true,
               headers: { "X-Goog-Upload-Command": "start", "X-Goog-Upload-Header-Content-Type": "application/pdf" },
               postDataEntries: [{ bytes: b64(enc("File name=Farah_CV.pdf")) }] },
  });
  let o = outcome();
  assert.equal(new URLSearchParams(sentBody(o).toString()).get("File name"), "Hager_CV.pdf");

  resetCalls();
  await fire({
    requestId: "g2",
    request: { url: base + "?upload_id=1", method: "POST", hasPostData: true,
               headers: { "X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0" },
               postDataEntries: [{ bytes: b64(PDF) }] },
  });
  o = outcome();
  assert.ok(o.cont && !o.failed);
  assert.equal(sentBody(o).subarray(0, 7).toString(), "MASKED:");
});

test("Gemini chunked upload (offset != 0) → BLOCKED", async () => {
  resetCalls();
  await fire({
    requestId: "g3",
    request: { url: "https://push.clients6.google.com/upload/?upload_id=1", method: "POST", hasPostData: true,
               headers: { "X-Goog-Upload-Command": "upload", "X-Goog-Upload-Offset": "262144" },
               postDataEntries: [{ bytes: b64(PDF) }] },
  });
  assert.ok(outcome().failed);
});

test("Gemini multipart upload: file bytes and filename masked", async () => {
  resetCalls();
  const { buildMultipart } = await import("../src/background/body.js");
  const B = "----X";
  const mp = buildMultipart([{
    headers: [["Content-Disposition", 'form-data; name="file"; filename="Farah.pdf"'], ["Content-Type", "application/pdf"]],
    bytes: PDF, name: "file", filename: "Farah.pdf", contentType: "application/pdf",
  }], B);
  await fire({
    requestId: "g4",
    request: { url: "https://content-push.googleapis.com/upload", method: "POST", hasPostData: true,
               headers: { "Content-Type": `multipart/form-data; boundary=${B}` },
               postDataEntries: [{ bytes: b64(mp) }] },
  });
  const o = outcome();
  assert.ok(o.cont && !o.failed);
  const out = sentBody(o).toString("latin1");
  assert.ok(out.includes('filename="Hager.pdf"'));
  assert.ok(out.includes("MASKED:"));
});

test("unrelated request passes through untouched", async () => {
  resetCalls();
  await fire({ requestId: "p1", request: { url: "https://chatgpt.com/backend-api/me", method: "GET", headers: {} } });
  const o = outcome();
  assert.ok(o.cont && !o.cont.postData && !o.failed);
});

// ── contract with the real backend (shapes copied from local-backend/app/api/*.py) ──

const lastLog = async () => ((await chrome.storage.local.get("activityLog")).activityLog || [])[0];
const sendReq = (id, text) => fire({
  requestId: id,
  request: { url: "https://chatgpt.com/backend-api/f/conversation", method: "POST", hasPostData: true, headers: {},
             postDataEntries: [{ bytes: b64(enc(JSON.stringify({ conversation_id: null,
               messages: [{ author: { role: "user" }, content: { parts: [text] } }] }))) }] },
});
const zipLike = (marker) => Buffer.concat([Buffer.from([0x50, 0x4b, 3, 4]), Buffer.alloc(40), Buffer.from(marker), Buffer.alloc(200)]);
const putFile = (id, bytes, headers = {}) => fire({
  requestId: id,
  request: { url: AZ, method: "PUT", hasPostData: true, headers, postDataEntries: [{ bytes: b64(bytes) }] },
});

test("image upload → blocked with a readable reason (backend answers 400 image_file)", async () => {
  resetCalls();
  await putFile("c1", Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]), { "content-type": "image/png" });
  assert.ok(outcome().failed && !outcome().cont);
  assert.match((await lastLog()).detail, /Images can't be masked/);
});

test("unknown file type (e.g. .zip/.mp3) → blocked with a readable reason", async () => {
  resetCalls();
  await putFile("c2", Buffer.from([0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 9, 9]), {});
  assert.ok(outcome().failed);
  assert.match((await lastLog()).detail, /Unsupported file type/);
});

test("nameless Word upload: extension is derived from the ZIP contents so the backend treats it as .docx", async () => {
  resetCalls();
  await putFile("c3", zipLike("word/document.xml"), { "content-type": "application/octet-stream" });
  const o = outcome();
  assert.ok(o.cont && !o.failed, "must be masked, not rejected as unknown");
  assert.match(lastFile.filename, /\.docx$/);
});

test("nameless Excel upload → .xlsx", async () => {
  resetCalls();
  await putFile("c4", zipLike("xl/workbook.xml"), {});
  assert.ok(outcome().cont && !outcome().failed);
  assert.match(lastFile.filename, /\.xlsx$/);
});

test("a .docx whose reserved name lost its extension still reaches the backend as .docx", async () => {
  resetCalls();
  await fire({
    requestId: "c5a",
    request: { url: "https://chatgpt.com/backend-api/files", method: "POST", hasPostData: true, headers: {},
               postDataEntries: [{ bytes: b64(enc(JSON.stringify({ file_name: "report", file_size: 9 }))) }] },
  });
  resetCalls();
  await putFile("c5", zipLike("word/document.xml"), {});
  assert.ok(outcome().cont);
  assert.equal(lastFile.filename, "report.docx");
});

test("file masking counts the backend's replacements in stats (X-DLP-Replacements)", async () => {
  resetCalls();
  const before = (await chrome.storage.local.get(["entitiesMasked", "filesMasked"]));
  await putFile("c6", Buffer.from("%PDF-1.7 hello"), { "content-type": "application/pdf" });
  const after = (await chrome.storage.local.get(["entitiesMasked", "filesMasked"]));
  assert.equal(after.filesMasked, (before.filesMasked || 0) + 1);
  assert.equal(after.entitiesMasked, (before.entitiesMasked || 0) + 3);
});

test("partial detector coverage is passed through but RECORDED (not hidden)", async () => {
  resetCalls();
  maskOverride = { degraded: true, coverage_complete: false, uncovered_labels: ["PERSON", "ORGANIZATION"], degraded_reasons: ["tier2:timeout"] };
  await sendReq("c7", "hello Farah");
  assert.ok(outcome().cont && !outcome().failed);
  assert.match((await lastLog()).detail, /partial coverage — not checked: PERSON, ORGANIZATION/);
});

test("strict backend (safe_to_send=false, no leaks, coverage incomplete) → blocked with the right reason", async () => {
  resetCalls();
  maskOverride = { safe_to_send: false, coverage_complete: false, degraded: true, strict: true, uncovered_labels: ["PERSON"] };
  await sendReq("c8", "hello Farah");
  assert.ok(outcome().failed && !outcome().cont);
  assert.match((await lastLog()).detail, /coverage incomplete \(strict mode\)/);
});

test("backend reports a residual leak → blocked, leak TYPES logged (never values)", async () => {
  resetCalls();
  maskOverride = { safe_to_send: false, leaks: [{ type: "EMAIL", value: "do-not-log@x.com" }] };
  await sendReq("c9", "hello");
  assert.ok(outcome().failed);
  const log = await lastLog();
  assert.deepEqual(log.entityTypes, ["EMAIL"]);
  assert.ok(!JSON.stringify(log).includes("do-not-log"));
});

test("prompt over the backend limit (413) → blocked with a readable reason", async () => {
  resetCalls();
  await sendReq("c10", "x".repeat(200001));
  assert.ok(outcome().failed);
  assert.match((await lastLog()).detail, /Prompt too long/);
});

test("rotated install token: the 401 is recovered once (token refetched), request is still masked", async () => {
  resetCalls();
  const fetchesBefore = tokenFetches;
  serverToken = "t2"; // backend regenerated ~/.pii_gateway_token.json; extension still caches "t1"
  await sendReq("c11", "hi Farah");
  const o = outcome();
  assert.ok(o.cont && !o.failed, "must recover, not block");
  assert.equal(JSON.parse(sentBody(o).toString()).messages[0].content.parts[0], "hi Hager");
  assert.equal(tokenFetches, fetchesBefore + 1);
});

test("backend 422 on a file (residual_leak) → blocked with a readable reason", async () => {
  resetCalls();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => String(url).endsWith("/api/process_file")
    ? jsonErr(422, { error: "residual_leak", blockers: [], warnings: [], leak_types: ["EMAIL"] })
    : realFetch(url, opts);
  try { await putFile("c12", Buffer.from("%PDF-1.7 x"), {}); } finally { globalThis.fetch = realFetch; }
  assert.ok(outcome().failed);
  assert.match((await lastLog()).detail, /still contained sensitive data/);
});


// ── Browser-side file masking (content script → MASK_SELECTED_FILE) ─────────────────────
const CHATGPT_SENDER = { tab: { id: 7, url: "https://chatgpt.com/c/abc" } };
const sendMsg = (message, sender = CHATGPT_SENDER) =>
  new Promise((resolve) => { onMessage(message, sender, resolve); });
const selected = (bytes, name = "report.pdf") =>
  ({ type: "MASK_SELECTED_FILE", filename: name, contentType: "application/pdf", bytesBase64: b64(bytes) });

test("MASK_SELECTED_FILE: masks via backend and returns masked bytes", async () => {
  resetCalls();
  const r = await sendMsg(selected(PDF));
  assert.equal(r.success, true);
  const out = Buffer.from(r.bytesBase64, "base64");
  assert.equal(out.subarray(0, 7).toString(), "MASKED:");
  assert.deepEqual([...out.subarray(7)], [...PDF]);
  assert.equal(r.filename, "report.pdf");
});

test("the browser-masked file is verified on the network by hash, NOT masked twice", async () => {
  resetCalls();
  const r = await sendMsg(selected(PDF, "twice.pdf"));
  const masked = Buffer.from(r.bytesBase64, "base64");
  lastFile = null;
  await fire({
    requestId: "bm1",
    request: { url: AZ, method: "PUT", hasPostData: true, headers: { "content-type": "application/pdf" },
               postDataEntries: [{ bytes: b64(masked) }] },
  });
  const o = outcome();
  assert.ok(o.cont && !o.failed, "must continue");
  assert.equal(o.cont.postData, undefined, "body is forwarded unchanged (no second masking)");
  assert.equal(lastFile, null, "backend was not called again");
});

test("an authorization is single-use: a second identical upload is not waved through", async () => {
  resetCalls();
  const unique = Uint8Array.from([0x25, 0x50, 0x44, 0x46, 7, 7, 7, 7]);   // not authorized by earlier tests
  const r = await sendMsg(selected(unique, "once.pdf"));
  const masked = Buffer.from(r.bytesBase64, "base64");
  const put = (id) => fire({ requestId: id, request: { url: AZ, method: "PUT", hasPostData: true,
    headers: { "content-type": "application/pdf" }, postDataEntries: [{ bytes: b64(masked) }] } });
  await put("s1");
  calls = [];
  await put("s2");
  const o = outcome();
  // (the fake backend rejects already-masked bytes as an unknown type, so "blocked" is also correct)
  assert.ok(o.failed || (o.cont && o.cont.postData), "must NOT be forwarded unchanged via a spent authorization");
});

test("an upload that does NOT match what was masked is still masked/blocked by the network path", async () => {
  resetCalls();
  await sendMsg(selected(PDF, "a.pdf"));
  calls = [];
  const other = Uint8Array.from([0x25, 0x50, 0x44, 0x46, 9, 9, 9]);
  await fire({ requestId: "mm1", request: { url: AZ, method: "PUT", hasPostData: true,
    headers: { "content-type": "application/pdf" }, postDataEntries: [{ bytes: b64(other) }] } });
  const o = outcome();
  assert.equal(Buffer.from(o.cont.postData, "base64").subarray(0, 7).toString(), "MASKED:");
});

test("MASK_SELECTED_FILE from a non-ChatGPT tab is refused", async () => {
  resetCalls();
  const r = await sendMsg(selected(PDF), { tab: { id: 9, url: "https://evil.example/" } });
  assert.equal(r.success, false);
});

test("MASK_SELECTED_FILE: backend down → error (fail closed), no bytes returned", async () => {
  resetCalls();
  backendUp = false;
  const r = await sendMsg(selected(PDF));
  assert.equal(r.success, false);
  assert.equal(r.bytesBase64, undefined);
});

test("MASK_SELECTED_FILE: unsupported file (image) → error with a readable reason", async () => {
  resetCalls();
  const png = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4]);
  const r = await sendMsg({ ...selected(png, "pic.png"), contentType: "image/png" });
  assert.equal(r.success, false);
  assert.ok(r.error && r.error.length > 0);
});


// ── Reply demasking: GET_MAPPING (page-side demasker asks the worker) ─────────────────────
test("GET_MAPPING: returns the conversation's entries from the backend", async () => {
  resetCalls(); mappingCalls = []; mappingDown = false;
  const r = await sendMsg({ type: "GET_MAPPING", sinceVersion: null });
  assert.equal(r.success, true);
  assert.equal(r.enabled, true);
  assert.deepEqual(r.entries, [{ fake: "Hager", real: "Farah" }]);
  assert.match(mappingCalls[0].conversation_id, /^(new_|conv_)/);
});

test("GET_MAPPING: same version → changed:false and no entries", async () => {
  resetCalls(); mappingCalls = [];
  const r = await sendMsg({ type: "GET_MAPPING", sinceVersion: 4 });
  assert.equal(r.changed, false);
  assert.deepEqual(r.entries, []);
});

test("GET_MAPPING uses the SAME vault id as masking in that tab", async () => {
  resetCalls(); mappingCalls = [];
  await sendMsg({ type: "GET_MAPPING", sinceVersion: null });
  const first = mappingCalls[0].conversation_id;
  mappingCalls = [];
  await sendMsg({ type: "GET_MAPPING", sinceVersion: null });
  assert.equal(mappingCalls[0].conversation_id, first, "stable per tab/conversation");
});

test("GET_MAPPING: off switch (popup) → enabled:false, backend not asked", async () => {
  resetCalls(); mappingCalls = [];
  await chrome.storage.local.set({ demaskEnabled: false });
  const r = await sendMsg({ type: "GET_MAPPING", sinceVersion: null });
  await chrome.storage.local.set({ demaskEnabled: true });
  assert.equal(r.enabled, false);
  assert.equal(mappingCalls.length, 0);
});

test("GET_MAPPING: protection off → disabled; non-chat sender refused; backend error → failure", async () => {
  resetCalls();
  await chrome.storage.local.set({ protectionEnabled: false });
  assert.equal((await sendMsg({ type: "GET_MAPPING" })).enabled, false);
  await chrome.storage.local.set({ protectionEnabled: true });
  assert.equal((await sendMsg({ type: "GET_MAPPING" }, { tab: { id: 9, url: "https://evil.example/" } })).success, false);
  mappingDown = true;
  assert.equal((await sendMsg({ type: "GET_MAPPING" })).success, false);
  mappingDown = false;
});
