// Run with: npm test   (or: node --test tests/)
//
// Loads the REAL file-upload-override.js in a sandboxed `window`, the same
// way tests/fetch-override.test.js does for its file. This is a regression
// test for the bug fixed in this pass: chrome.runtime.sendMessage only
// JSON-serializes its payload, so passing a File/Blob through it directly
// silently turns into `{}` on the other side — the file never actually
// reached the backend, for ANY file type (pdf, docx, xlsx, csv, txt...).
// The fix base64-encodes the file before it crosses that boundary and
// decodes it back into a real Blob on each side. This test fails on the
// pre-fix code because `payload.file` would be undefined/empty rather than
// real bytes, and the mock backend below asserts on the actual byte content.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const MSG_TAG = "__PII_GATEWAY__";

function buildSandbox({ mockBackend, nativeFetch }) {
  const calls = { native: [], logs: [], stats: [] };
  const listeners = [];
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.console = console;
  sandbox.setTimeout = setTimeout;
  sandbox.clearTimeout = clearTimeout;
  sandbox.Response = Response;
  sandbox.Blob = Blob;
  sandbox.File = File;
  sandbox.FormData = FormData;
  sandbox.btoa = btoa;
  sandbox.atob = atob;

  sandbox.document = {};

  sandbox.addEventListener = (type, fn) => {
    if (type === "message") listeners.push(fn);
  };
  sandbox.removeEventListener = (type, fn) => {
    const i = listeners.indexOf(fn);
    if (i >= 0) listeners.splice(i, 1);
  };

  sandbox.fetch = async (input, init) => {
    calls.native.push({ input, init });
    return nativeFetch(input, init);
  };

  vm.createContext(sandbox);
  // See the long comment in fetch-override.test.js: the vm's in-context
  // `window` is not `===` the host-side `sandbox` object, and the
  // production code checks `event.source !== window`, so relayed
  // messages must use the in-vm reference.
  const vmWindow = vm.runInContext("window", sandbox);
  sandbox.postMessage = wrapPostMessage(vmWindow, mockBackend, calls, listeners);

  const source = fs.readFileSync(
    path.join(__dirname, "../src/content/file-upload-override.js"),
    "utf8"
  );
  new vm.Script(source, { filename: "file-upload-override.js" }).runInContext(sandbox);

  return { sandbox, calls };
}

function wrapPostMessage(vmWindow, mockBackend, calls, listeners) {
  return (data) => {
    if (!data || data.tag !== MSG_TAG) return;
    queueMicrotask(async () => {
      let response;
      if (data.type === "LOG_EVENT") {
        calls.logs.push(data.payload);
        response = { success: true };
      } else if (data.type === "INCREMENT_STAT") {
        calls.stats.push(data.payload);
        response = { success: true };
      } else {
        response = await mockBackend(data.type, data.payload);
      }
      for (const fn of listeners.slice()) {
        fn({ source: vmWindow, data: { tag: MSG_TAG, id: data.id, response } });
      }
    });
  };
}

function uploadInit(formData) {
  return { body: formData };
}

test("a real file survives the sendMessage boundary as actual bytes, not {}", async () => {
  const originalBytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 1, 2, 3, 250, 251]); // fake "%PDF" + junk
  let receivedPayload = null;

  const mockBackend = async (type, payload) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: true };
    if (type === "PROCESS_FILE") {
      receivedPayload = payload;
      // Echo back a DIFFERENT byte sequence as the "masked" file, so the
      // test can also confirm the round trip back into the FormData.
      const maskedBytes = new Uint8Array([9, 9, 9]);
      let binary = "";
      for (const b of maskedBytes) binary += String.fromCharCode(b);
      return {
        success: true,
        maskedFileBase64: btoa(binary),
        maskedContentType: "application/pdf",
      };
    }
    throw new Error("unexpected: " + type);
  };

  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async (input, init) => {
      calls.uploadedBody = init.body;
      return new Response("{}", { status: 200 });
    },
  });

  const file = new sandbox.File([originalBytes], "report.pdf", { type: "application/pdf" });
  const formData = new sandbox.FormData();
  formData.append("file", file);

  await sandbox.window.fetch("https://chatgpt.com/backend-api/files", uploadInit(formData));

  assert.ok(receivedPayload, "PROCESS_FILE must have been called");
  assert.ok(receivedPayload.dataBase64, "the file must cross as a base64 string, not a bare File/Blob");

  // Decode what the mock backend actually received and check it matches
  // the original bytes exactly — this is what the old code got wrong:
  // payload.file was a File object that JSON-serialized away to nothing.
  const decoded = Buffer.from(receivedPayload.dataBase64, "base64");
  assert.deepEqual(
    [...decoded],
    [...originalBytes],
    "the bytes the backend receives must be identical to the original file's bytes"
  );
  assert.equal(receivedPayload.filename, "report.pdf");
  assert.equal(receivedPayload.contentType, "application/pdf");

  // And the masked file that came back must make it into the upload that
  // actually goes out, as real bytes again (not left as base64 text).
  assert.ok(calls.uploadedBody instanceof sandbox.FormData, "the rewritten upload must use FormData");
  const uploadedFile = calls.uploadedBody.get("file");
  const uploadedBuffer = Buffer.from(await uploadedFile.arrayBuffer());
  assert.deepEqual([...uploadedBuffer], [9, 9, 9]);
});

test("files over the size cap are blocked fail-closed, never sent unmasked", async () => {
  const mockBackend = async (type) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: true };
    throw new Error("PROCESS_FILE must never be called for an oversized file: " + type);
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () => {
      throw new Error("native fetch must NOT be called for an oversized file");
    },
  });

  const bigBytes = new Uint8Array(21 * 1024 * 1024); // just over the 20MB cap
  const file = new sandbox.File([bigBytes], "huge.csv", { type: "text/csv" });
  const formData = new sandbox.FormData();
  formData.append("file", file);

  const response = await sandbox.window.fetch(
    "https://chatgpt.com/backend-api/files",
    uploadInit(formData)
  );

  assert.equal(response.status, 413);
  assert.equal(calls.native.length, 0);
});

test("escape hatch off: file goes out completely unmasked, and it's logged", async () => {
  const mockBackend = async (type) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: false };
    throw new Error("PROCESS_FILE must never be called while protection is off: " + type);
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () => new Response("{}", { status: 200 }),
  });

  const file = new sandbox.File([new Uint8Array([1, 2, 3])], "sheet.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const formData = new sandbox.FormData();
  formData.append("file", file);

  await sandbox.window.fetch("https://chatgpt.com/backend-api/upload", uploadInit(formData));

  assert.equal(calls.native.length, 1, "the request must still go out, unmasked");
  assert.ok(calls.logs.some((l) => l.event === "ESCAPE_HATCH_BYPASS_FILE"));
});
