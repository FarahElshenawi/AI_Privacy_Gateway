// Run with: npm test   (or: node --test tests/)
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { matchesPattern, extractMessageText, replaceMessageText } = require("../src/lib/message-text.js");

// --- matchesPattern ---

test("matchesPattern: matches when the URL contains one of the patterns", () => {
  assert.equal(matchesPattern("https://chatgpt.com/backend-api/conversation", ["/backend-api/conversation"]), true);
});

test("matchesPattern: false when none match", () => {
  assert.equal(matchesPattern("https://chatgpt.com/backend-api/me", ["/backend-api/conversation"]), false);
});

// --- extractMessageText ---

test("extractMessageText: pulls the user's text out of ChatGPT's request shape", () => {
  const body = JSON.stringify({
    messages: [{ role: "user", content: { parts: ["hello there"] } }],
  });
  assert.equal(extractMessageText(body), "hello there");
});

// Regression test: a live chatgpt.com request payload captured 2026-10-02
// put the role under msg.author.role, not msg.role directly — msg.role
// was absent entirely. The old code only ever checked msg.role, so this
// shape matched nothing, extractMessageText returned null for every real
// message, and fetch-override.js treated that as "nothing to mask" and
// let every prompt through completely unmasked with no error logged
// anywhere. This is the exact shape from that capture (trimmed to the
// fields that matter here).
test("extractMessageText: current ChatGPT shape — role nested under author.role, not on the message directly", () => {
  const body = JSON.stringify({
    action: "next",
    conversation_id: "6abff516-0464-83e9-ab95-50b1de4e42d9",
    messages: [
      {
        id: "7960cc9b-e165-4741-aaa3-8b64ad311f8e",
        author: { role: "user" },
        content: { content_type: "text", parts: ["hi my name is salma"] },
      },
    ],
  });
  assert.equal(extractMessageText(body), "hi my name is salma");
});

test("extractMessageText: joins multiple parts with a newline", () => {
  const body = JSON.stringify({
    messages: [{ role: "user", content: { parts: ["line one", "line two"] } }],
  });
  assert.equal(extractMessageText(body), "line one\nline two");
});

test("extractMessageText: ignores non-user roles (e.g. system/assistant)", () => {
  const body = JSON.stringify({
    messages: [
      { role: "system", content: { parts: ["system prompt"] } },
      { role: "user", content: { parts: ["actual user text"] } },
    ],
  });
  assert.equal(extractMessageText(body), "actual user text");
});

test("extractMessageText: returns null for malformed JSON rather than throwing", () => {
  assert.equal(extractMessageText("not json{{{"), null);
});

test("extractMessageText: returns null when there's no messages array", () => {
  assert.equal(extractMessageText(JSON.stringify({ foo: "bar" })), null);
});

test("extractMessageText: empty string input does not throw", () => {
  assert.doesNotThrow(() => extractMessageText(""));
});

test("extractMessageText: skips non-string parts (e.g. multimodal image parts)", () => {
  const body = JSON.stringify({
    messages: [{ role: "user", content: { parts: [{ type: "image", asset: "x" }, "the real text"] } }],
  });
  assert.equal(extractMessageText(body), "the real text");
});

// --- replaceMessageText ---

test("replaceMessageText: substitutes the masked text back into the body", () => {
  const body = JSON.stringify({
    messages: [{ role: "user", content: { parts: ["my email is j@x.com"] } }],
  });
  const result = replaceMessageText(body, "my email is j@x.com", "my email is [EMAIL_1]");
  const parsed = JSON.parse(result);
  assert.equal(parsed.messages[0].content.parts[0], "my email is [EMAIL_1]");
});

test("replaceMessageText: current ChatGPT shape — role nested under author.role", () => {
  const body = JSON.stringify({
    messages: [{ author: { role: "user" }, content: { parts: ["my email is j@x.com"] } }],
  });
  const result = replaceMessageText(body, "my email is j@x.com", "my email is [EMAIL_1]");
  const parsed = JSON.parse(result);
  assert.equal(parsed.messages[0].content.parts[0], "my email is [EMAIL_1]");
});

test("replaceMessageText: returns the original body unchanged on malformed JSON", () => {
  const body = "not json";
  assert.equal(replaceMessageText(body, "a", "b"), body);
});

test("replaceMessageText: leaves non-user messages untouched", () => {
  const body = JSON.stringify({
    messages: [
      { role: "system", content: { parts: ["contains j@x.com too"] } },
      { role: "user", content: { parts: ["j@x.com"] } },
    ],
  });
  const result = JSON.parse(replaceMessageText(body, "j@x.com", "[EMAIL_1]"));
  assert.equal(result.messages[0].content.parts[0], "contains j@x.com too");
  assert.equal(result.messages[1].content.parts[0], "[EMAIL_1]");
});
