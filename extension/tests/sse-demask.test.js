// Run with: npm test   (or: node --test tests/)
//
// Node 18+ has ReadableStream/TextEncoder/TextDecoder as globals, so this
// runs the REAL implementation with no browser needed.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createDemaskingStream } = require("../src/lib/sse-demask.js");

// --- test helpers ---

// Builds an SSE ReadableStream from a list of "events" — either a string
// of text (becomes a {message:{content:{parts:[text]}}} frame) or the
// literal "[DONE]". Optionally split into N bytes/chars per underlying
// stream chunk, to simulate the network delivering a few bytes at a time
// (the exact scenario where a token can land split across chunks).
function sseStream(events, { chunkChars = Infinity } = {}) {
  let raw = "";
  for (const ev of events) {
    if (ev === "[DONE]") {
      raw += "data: [DONE]\n\n";
    } else {
      raw += `data: ${JSON.stringify({ message: { content: { parts: [ev] } } })}\n\n`;
    }
  }
  const encoder = new TextEncoder();
  const bytes = encoder.encode(raw);
  let offset = 0;
  return new ReadableStream({
    pull(controller) {
      if (offset >= bytes.length) {
        controller.close();
        return;
      }
      const end = Math.min(offset + chunkChars, bytes.length);
      controller.enqueue(bytes.slice(offset, end));
      offset = end;
    },
  });
}

async function collectText(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let out = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    out += decoder.decode(value, { stream: true });
  }
  return out;
}

// Extracts, in order, every parts[0] value actually sent to the page.
function extractEmittedTexts(sseText) {
  const texts = [];
  for (const line of sseText.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    const data = line.slice(6).trim();
    if (data === "[DONE]" || !data) continue;
    try {
      const json = JSON.parse(data);
      const parts = json.message?.content?.parts;
      if (parts && parts[0]) texts.push(parts[0]);
    } catch {
      // ignore
    }
  }
  return texts;
}

// A fake /demask: replaces every occurrence of each (fake -> real) pair.
// Mirrors the backend's vault.restore_text — a literal string replace, so
// a FAKE value split across two separate demask() calls (rather than
// held together as one complete substring) will NOT be found by either
// call, exactly like the real vault lookup.
function makeFakeDemask(pairs) {
  return async (text) => {
    let restored = text;
    for (const [fake, real] of Object.entries(pairs)) {
      restored = restored.split(fake).join(real);
    }
    return { success: true, restoredText: restored };
  };
}

// --- tests ---

test("passes through plain text with no masked tokens unchanged", async () => {
  const demask = makeFakeDemask({});
  const stream = createDemaskingStream(sseStream(["hello ", "world", "[DONE]"]), demask, {
    flushThreshold: 5,
    safetyMargin: 3,
  });
  const out = await collectText(stream);
  assert.equal(extractEmittedTexts(out).join(""), "hello world");
  assert.ok(out.includes("data: [DONE]"));
});

test("a token split across MANY tiny chunks is still fully restored (the core regression case)", async () => {
  // "FAKE_NAME_123" (13 chars) sits right around where a naive fixed-size
  // flush would have cut the buffer. Deliver it, and the surrounding
  // text, one character at a time as separate SSE frames — the worst
  // case for a chunk-boundary bug.
  const prefix = "a".repeat(12); // 12 chars
  const token = "FAKE_NAME_123"; // 13 chars — straddles a cut at 20 with no margin
  const suffix = "b".repeat(30); // enough trailing text to force multiple flushes
  const fullMasked = prefix + token + suffix;

  const demask = makeFakeDemask({ [token]: "Dr. Amina Hassan" });

  const events = fullMasked.split(""); // one character per SSE frame
  events.push("[DONE]");

  const stream = createDemaskingStream(sseStream(events), demask, {
    flushThreshold: 10,
    safetyMargin: 8,
  });

  const out = await collectText(stream);
  const finalText = extractEmittedTexts(out).join("");

  assert.equal(finalText, prefix + "Dr. Amina Hassan" + suffix);
  // The raw fake token must never appear in what was sent to the page —
  // that would mean it leaked out un-demasked.
  assert.ok(!finalText.includes(token), "the fake token must not leak into the visible response");
});

test("replacement text shorter than the fake token does not corrupt later text (length-drift regression)", async () => {
  // "[REDACTED]" (10 chars) -> "" (0 chars) is the most aggressive length
  // change possible. If output offsets were (incorrectly) borrowed from
  // masked-text positions, this would desync everything after it.
  const demask = makeFakeDemask({ "[REDACTED]": "" });
  const events = ["before-", "[REDACTED]", "-after"];
  events.push("[DONE]");

  const stream = createDemaskingStream(sseStream(events), demask, {
    flushThreshold: 1,
    safetyMargin: 1,
  });

  const out = await collectText(stream);
  assert.equal(extractEmittedTexts(out).join(""), "before--after");
});

test("a fake value LONGER than its real replacement, repeated many times, stays aligned", async () => {
  // Stresses cumulative length drift across several replacements in one
  // stream, which is exactly what a naive offset-based implementation
  // gets wrong more and more as the stream goes on.
  const demask = makeFakeDemask({ "SyntheticPersonName": "Al" });
  const events = [];
  for (let i = 0; i < 5; i++) {
    events.push("x ", "SyntheticPersonName", " y ");
  }
  events.push("[DONE]");

  const stream = createDemaskingStream(sseStream(events), demask, {
    flushThreshold: 6,
    safetyMargin: 4,
  });

  const out = await collectText(stream);
  const expected = "x Al y ".repeat(5);
  assert.equal(extractEmittedTexts(out).join(""), expected);
});

test("[DONE] never arrives before the trailing text it would otherwise race", async () => {
  const demask = makeFakeDemask({ TOKEN: "resolved" });
  const events = ["lead-in ", "TOKEN", " trailing-tail", "[DONE]"];

  const stream = createDemaskingStream(sseStream(events), demask, {
    flushThreshold: 1000, // never trips on its own — only the forced flush before [DONE] should emit anything
    safetyMargin: 1000,
  });

  const out = await collectText(stream);
  const doneIndex = out.indexOf("[DONE]");
  const lastTextIndex = out.lastIndexOf("trailing-tail");
  assert.ok(doneIndex > -1 && lastTextIndex > -1, "expected both [DONE] and the trailing text to appear");
  assert.ok(lastTextIndex < doneIndex, "all text must be flushed before [DONE] is emitted");
  assert.equal(extractEmittedTexts(out).join(""), "lead-in resolved trailing-tail");
});

test("when /demask keeps failing mid-stream, nothing un-demasked is emitted (fails closed, not open)", async () => {
  let shouldFail = true;
  const demask = async (text) => {
    if (shouldFail) return { success: false, error: "backend down" };
    return { success: true, restoredText: text.split("TOKEN").join("Real Value") };
  };

  const events = ["a".repeat(5), "TOKEN", "b".repeat(5)];
  const stream = createDemaskingStream(sseStream(events), demask, {
    flushThreshold: 3,
    safetyMargin: 3,
  });

  const reader = stream.getReader();
  // First pull should get nothing useful yet (demask failing, non-forced flush bails out).
  const first = await reader.read();
  if (!first.done) {
    const text = new TextDecoder().decode(first.value);
    assert.ok(!text.includes("TOKEN"), "must not emit the masked token while demask is failing");
  }
  reader.cancel();
});

test("empty stream (no events at all) closes cleanly", async () => {
  const demask = makeFakeDemask({});
  const stream = createDemaskingStream(sseStream([]), demask);
  const out = await collectText(stream);
  assert.equal(out, "");
});

test("works even when every SSE frame arrives in one single network chunk", async () => {
  const demask = makeFakeDemask({ SECRET: "ok" });
  const stream = createDemaskingStream(
    sseStream(["hello ", "SECRET", " world", "[DONE]"]),
    demask,
    { flushThreshold: 1, safetyMargin: 1 }
  );
  const out = await collectText(stream);
  assert.equal(extractEmittedTexts(out).join(""), "hello ok world");
});
