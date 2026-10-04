// Run with: npm test   (or: node --test tests/)
//
// Regression test for the single most important bug fixed in this pass:
// local-backend/app/api/mask.py returns snake_case JSON (masked_text,
// safe_to_send, entities_found — standard FastAPI/Pydantic serialization).
// service-worker.js's maskText() is the ONE seam that's supposed to
// convert that into the camelCase shape the rest of the extension reads
// (maskedText, safeToSend, entitiesFound). An earlier version did
// `{ success: true, ...data }`, which left those fields undefined and
// caused fetch-override.js to treat EVERY prompt as an unsafe residual
// leak and block it — the extension could not send a single message.
//
// This loads the REAL service-worker.js source in a sandboxed context
// (no browser needed) with a mocked chrome.* and fetch, so it exercises
// the actual production code, not a re-description of it.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

function loadServiceWorker({ fetchImpl } = {}) {
  const source = fs.readFileSync(
    path.join(__dirname, "../src/background/service-worker.js"),
    "utf8"
  );

  const listeners = { onMessage: null, onInstalled: null };
  const storage = {};

  const chrome = {
    storage: {
      local: {
        get: (key) =>
          Promise.resolve(
            typeof key === "string"
              ? { [key]: storage[key] }
              : Object.fromEntries(Object.keys(storage).filter((k) => key.includes?.(k) ?? true).map((k) => [k, storage[k]]))
          ),
        set: (obj) => {
          Object.assign(storage, obj);
          return Promise.resolve();
        },
        remove: (key) => {
          delete storage[key];
          return Promise.resolve();
        },
      },
    },
    runtime: {
      onMessage: { addListener: (fn) => (listeners.onMessage = fn) },
      onInstalled: { addListener: (fn) => (listeners.onInstalled = fn) },
    },
  };

  const sandbox = {
    chrome,
    fetch: fetchImpl,
    console,
    setTimeout,
    clearTimeout,
  };
  vm.createContext(sandbox);
  new vm.Script(source, { filename: "service-worker.js" }).runInContext(sandbox);

  return { listeners, storage };
}

function sendMessage(listeners, message) {
  return new Promise((resolve) => {
    const keepAsync = listeners.onMessage(message, {}, resolve);
    assert.equal(keepAsync, true, "the listener must return true to keep the async sendResponse alive");
  });
}

test("maskText() converts the backend's snake_case response to the camelCase shape callers expect", async () => {
  const fetchImpl = async (url) => {
    if (url.endsWith("/token")) {
      return { ok: true, json: async () => ({ token: "tok123" }) };
    }
    if (url.endsWith("/api/mask")) {
      return {
        ok: true,
        json: async () => ({
          masked_text: "Hi [PERSON_1], email [EMAIL_1]",
          pairs: [["John", "[PERSON_1]"]],
          entities_found: 2,
          leaks: [],
          safe_to_send: true,
        }),
      };
    }
    throw new Error("unexpected fetch: " + url);
  };

  const { listeners } = loadServiceWorker({ fetchImpl });

  const response = await sendMessage(listeners, {
    type: "MASK",
    payload: { text: "Hi John, email john@x.com", conversationId: "c1" },
  });

  assert.equal(response.success, true);
  // This is the exact assertion that would have caught the original bug:
  // these fields must be present and camelCase, not undefined.
  assert.equal(response.maskedText, "Hi [PERSON_1], email [EMAIL_1]");
  assert.equal(response.entitiesFound, 2);
  assert.equal(response.safeToSend, true);
  assert.deepEqual(response.leaks, []);
});

test("safeToSend reflects a real leak (false), it is not just always-undefined", async () => {
  const fetchImpl = async (url) => {
    if (url.endsWith("/token")) return { ok: true, json: async () => ({ token: "t" }) };
    if (url.endsWith("/api/mask")) {
      return {
        ok: true,
        json: async () => ({
          masked_text: "still has a secret in it",
          pairs: [],
          entities_found: 0,
          leaks: [{ type: "API_KEY", text: "sk-..." }],
          safe_to_send: false,
        }),
      };
    }
    throw new Error("unexpected fetch: " + url);
  };

  const { listeners } = loadServiceWorker({ fetchImpl });
  const response = await sendMessage(listeners, {
    type: "MASK",
    payload: { text: "whatever", conversationId: "c1" },
  });

  assert.equal(response.safeToSend, false);
  assert.equal(response.leaks.length, 1);
});

test("a non-OK backend response is reported as a failure, not spread into the result", async () => {
  const fetchImpl = async (url) => {
    if (url.endsWith("/token")) return { ok: true, json: async () => ({ token: "t" }) };
    if (url.endsWith("/api/mask")) return { ok: false, status: 503 };
    throw new Error("unexpected fetch: " + url);
  };

  const { listeners } = loadServiceWorker({ fetchImpl });
  const response = await sendMessage(listeners, {
    type: "MASK",
    payload: { text: "hi", conversationId: "c1" },
  });

  assert.equal(response.success, false);
  assert.match(response.error, /503/);
});

test("demaskText() already converted restored_text -> restoredText correctly (no regression here)", async () => {
  const fetchImpl = async (url) => {
    if (url.endsWith("/token")) return { ok: true, json: async () => ({ token: "t" }) };
    if (url.endsWith("/api/demask")) {
      return { ok: true, json: async () => ({ restored_text: "Hi John", replacements_made: 1 }) };
    }
    throw new Error("unexpected fetch: " + url);
  };

  const { listeners } = loadServiceWorker({ fetchImpl });
  const response = await sendMessage(listeners, {
    type: "DEMASK",
    payload: { text: "Hi [PERSON_1]", conversationId: "c1" },
  });

  assert.equal(response.success, true);
  assert.equal(response.restoredText, "Hi John");
});
