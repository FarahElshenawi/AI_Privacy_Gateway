// Run with: npm test   (or: node --test tests/)
//
// Loads the REAL fetch-override.js in a sandboxed `window`, with the
// content-script <-> service-worker relay stubbed out by a single
// `mockBackend(type, payload)` function the test controls. This exercises
// the actual production file — escape hatch, fail-closed blocking, and
// the mask-success rewrite path — not a re-description of it.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const MSG_TAG = "__PII_GATEWAY__";
const messageText = require("../src/lib/message-text.js");
const sseDemask = require("../src/lib/sse-demask.js");

function buildSandbox({ mockBackend, nativeFetch }) {
  const calls = { native: [], logs: [], stats: [] };

  const listeners = [];
  const sandbox = {};
  sandbox.window = sandbox;
  sandbox.console = console;
  sandbox.setInterval = () => 0; // no-op: don't let the re-wrap timer run during tests
  sandbox.clearInterval = () => {};
  sandbox.setTimeout = setTimeout;
  sandbox.clearTimeout = clearTimeout;
  sandbox.Response = Response;
  sandbox.ReadableStream = ReadableStream;
  sandbox.TextEncoder = TextEncoder;
  sandbox.TextDecoder = TextDecoder;
  sandbox.PIIGatewayMessageText = messageText;
  sandbox.PIIGatewaySSEDemask = sseDemask;

  sandbox.document = {
    getElementById: () => null,
    createElement: () => ({ style: {}, remove() {} }),
    documentElement: { appendChild() {} },
  };

  sandbox.addEventListener = (type, fn) => {
    if (type === "message") listeners.push(fn);
  };
  sandbox.removeEventListener = (type, fn) => {
    const i = listeners.indexOf(fn);
    if (i >= 0) listeners.splice(i, 1);
  };
  // postMessage is wired up below, after vm.createContext(), because it
  // needs the `window` reference as seen FROM INSIDE the vm (see the long
  // comment further down) — not this host-side `sandbox` variable.

  // "native fetch" — whatever window.fetch is BEFORE fetch-override.js
  // runs. fetch-override.js captures this once and the composed override
  // ultimately calls it for every request it decides to forward.
  sandbox.fetch = async (input, init) => {
    calls.native.push({ input, init });
    return nativeFetch(input, init);
  };

  vm.createContext(sandbox);
  // IMPORTANT: vm.createContext() does NOT make the vm's internal global
  // object strictly `===` the `sandbox` object as seen from host code (a
  // well-known vm-module gotcha) — it wraps/proxies it. The code under
  // test does `if (event.source !== window) return;` to filter its own
  // postMessage traffic, exactly as it would in a real browser. To
  // satisfy that check from host-side message-relay code, we must use
  // the `window` reference as resolved FROM INSIDE the vm, not our host
  // `sandbox` variable — otherwise every relayed response is silently
  // dropped and sendToBackground() only ever resolves via its 30s
  // fail-closed timeout (which is what made this hang before the fix).
  const vmWindow = vm.runInContext("window", sandbox);
  sandbox.postMessage = wrapPostMessage(sandbox, vmWindow, mockBackend, calls, listeners);

  const source = fs.readFileSync(path.join(__dirname, "../src/content/fetch-override.js"), "utf8");
  new vm.Script(source, { filename: "fetch-override.js" }).runInContext(sandbox);

  return { sandbox, calls };
}

function wrapPostMessage(sandbox, vmWindow, mockBackend, calls, listeners) {
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

function conversationRequestInit(userText) {
  return {
    body: JSON.stringify({ messages: [{ role: "user", content: { parts: [userText] } }] }),
  };
}

function jsonResponse(obj, opts = {}) {
  return new Response(JSON.stringify(obj), {
    headers: { "content-type": "application/json" },
    status: opts.status ?? 200,
  });
}

// --- tests ---

test("escape hatch: protection OFF sends the ORIGINAL unmasked body and logs the bypass", async () => {
  const mockBackend = async (type) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: false };
    throw new Error("MASK should never be called while protection is off: " + type);
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () => jsonResponse({ ok: true }),
  });

  await sandbox.window.fetch(
    "https://chatgpt.com/backend-api/conversation",
    conversationRequestInit("my email is j@x.com")
  );

  assert.equal(calls.native.length, 1, "the request must still go out (unmasked), not be dropped");
  const sentBody = JSON.parse(calls.native[0].init.body);
  assert.equal(sentBody.messages[0].content.parts[0], "my email is j@x.com", "body must be unchanged — this IS the bypass");

  assert.ok(calls.logs.some((l) => l.event === "ESCAPE_HATCH_BYPASS"), "the bypass must be logged");
  assert.ok(calls.stats.some((s) => s.key === "bypassCount"), "the bypass must be counted");
});

test("fail-closed: a backend masking failure blocks the request with 503 and logs it", async () => {
  const mockBackend = async (type) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: true };
    if (type === "MASK") return { success: false, error: "backend unreachable" };
    throw new Error("unexpected: " + type);
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () => {
      throw new Error("native fetch must NOT be called when masking fails");
    },
  });

  const response = await sandbox.window.fetch(
    "https://chatgpt.com/backend-api/conversation",
    conversationRequestInit("hello")
  );

  assert.equal(response.status, 503);
  assert.equal(calls.native.length, 0, "the request must never reach the network");
  assert.ok(calls.logs.some((l) => l.event === "BLOCKED_MASK_FAILURE"));
  assert.ok(calls.stats.some((s) => s.key === "blocksCount"));
});

test("fail-closed: safeToSend === false (residual leak) blocks with 422", async () => {
  const mockBackend = async (type) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: true };
    if (type === "MASK") {
      return {
        success: true,
        maskedText: "still has sk-abc123 in it",
        entitiesFound: 0,
        leaks: [{ type: "API_KEY" }],
        safeToSend: false,
      };
    }
    throw new Error("unexpected: " + type);
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () => {
      throw new Error("native fetch must NOT be called when the residual scanner finds a leak");
    },
  });

  const response = await sandbox.window.fetch(
    "https://chatgpt.com/backend-api/conversation",
    conversationRequestInit("my key is sk-abc123")
  );

  assert.equal(response.status, 422);
  assert.equal(calls.native.length, 0);
  assert.ok(calls.logs.some((l) => l.event === "BLOCKED_RESIDUAL_LEAK"));
});

test("happy path: mask success rewrites the body and the masked version is what goes out", async () => {
  const mockBackend = async (type, payload) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: true };
    if (type === "MASK") {
      return {
        success: true,
        maskedText: "my email is [EMAIL_1]",
        entitiesFound: 1,
        leaks: [],
        safeToSend: true,
      };
    }
    if (type === "DEMASK") {
      return { success: true, restoredText: payload.text.replace("[EMAIL_1]", "j@x.com") };
    }
    throw new Error("unexpected: " + type);
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () =>
      new Response(JSON.stringify({ reply: "got it, [EMAIL_1]" }), {
        headers: { "content-type": "application/json" },
      }),
  });

  const response = await sandbox.window.fetch(
    "https://chatgpt.com/backend-api/conversation",
    conversationRequestInit("my email is j@x.com")
  );

  assert.equal(calls.native.length, 1);
  const sentBody = JSON.parse(calls.native[0].init.body);
  assert.equal(sentBody.messages[0].content.parts[0], "my email is [EMAIL_1]", "the MASKED text must be what's sent over the wire");

  const finalText = await response.text();
  assert.ok(finalText.includes("j@x.com"), "the non-streaming response must come back demasked to the real value");
  assert.ok(calls.stats.some((s) => s.key === "promptsMasked"));
  assert.ok(calls.stats.some((s) => s.key === "entitiesMasked"));
});

test("non-conversation URLs pass straight through with no masking involved", async () => {
  const mockBackend = async () => {
    throw new Error("the backend must never be contacted for a non-conversation URL");
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () => jsonResponse({ unrelated: true }),
  });

  const response = await sandbox.window.fetch("https://chatgpt.com/backend-api/me", { method: "GET" });
  assert.equal(calls.native.length, 1);
  const body = await response.json();
  assert.deepEqual(body, { unrelated: true });
});

// Regression test for a real bug seen live on chatgpt.com: the page
// itself reassigns window.fetch during its own startup (observed as the
// "window.fetch was reassigned by the page" log + FETCH_REWRAPPED_BY_PAGE
// audit event), and whatever page code captured a reference to
// window.fetch RIGHT AFTER doing that reassignment — e.g. to build its
// own instrumented fetch, or just to keep a local copy for later calls —
// ended up with a reference that bypassed our masking entirely. The old
// fix (poll every 500ms, reinstall if window.fetch isn't ours) loses this
// race: it's reactive, so there's a window after the page's reassignment
// but before our next poll tick where the page can read window.fetch and
// permanently capture something that was never ours. The actual fix
// (Object.defineProperty with a getter/setter) closes that window
// entirely — the page's assignment is intercepted and rewrapped
// SYNCHRONOUSLY, in the same statement, so nothing can ever read
// window.fetch and get back something unmasked.
test("page reassigning window.fetch, then immediately capturing a reference, still gets a masked fetch (closes the race the old polling fix missed)", async () => {
  const mockBackend = async (type) => {
    if (type === "GET_PROTECTION_ENABLED") return { success: true, enabled: true };
    if (type === "MASK") {
      return { success: true, maskedText: "hi [NAME_1]", entitiesFound: 1, leaks: [], safeToSend: true };
    }
    if (type === "DEMASK") {
      return { success: true, restoredText: "ok" };
    }
    throw new Error("unexpected: " + type);
  };
  const { sandbox, calls } = buildSandbox({
    mockBackend,
    nativeFetch: async () => jsonResponse({ ok: true }),
  });

  // Simulate the page's own startup code: it reassigns window.fetch to
  // its own wrapper (e.g. for analytics/telemetry — doesn't matter what
  // it does, only that it calls through to whatever window.fetch was
  // when it ran) ...
  const pageOwnFetch = sandbox.window.fetch; // what the page sees at reassignment time
  sandbox.window.fetch = function (input, init) {
    return pageOwnFetch.call(this, input, init);
  };

  // ... and in that SAME tick, something in the page's bundle grabs a
  // direct reference to window.fetch and uses ONLY that reference from
  // now on — never reading window.fetch again. This is exactly the
  // pattern that defeated the old 500ms poll.
  const capturedReference = sandbox.window.fetch;

  const response = await capturedReference.call(
    sandbox.window,
    "https://chatgpt.com/backend-api/conversation",
    conversationRequestInit("hi John")
  );

  assert.equal(response.status, 200, "the request must still succeed, not be dropped");
  assert.equal(calls.native.length, 1);
  const sentBody = JSON.parse(calls.native[0].init.body);
  assert.equal(
    sentBody.messages[0].content.parts[0],
    "hi [NAME_1]",
    "even through a reference captured immediately after the page's own reassignment, the body that reaches the network must be the MASKED one"
  );
});
