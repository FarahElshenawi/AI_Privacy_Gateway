/**
 * SSE demasking with a true rolling buffer.
 *
 * Two bugs this exists to prevent:
 *
 * 1. A masked token (a fake name, "[REDACTED]", a "[ORG_1]" anchor) can
 *    land on either side of wherever the buffer gets cut for a /demask
 *    call. Cutting at a fixed size and demasking each piece independently
 *    (the original bug) splits a token into two unrelated halves that
 *    silently fail to match the vault — the fake value leaks into the
 *    visible response un-restored.
 *
 * 2. Masking/demasking changes string length (a fake name is essentially
 *    never the same length as the real one, "[REDACTED]" never matches
 *    the secret it replaced). That means you CANNOT take offsets measured
 *    in the masked text and use them to slice the demasked text — doing
 *    that (an earlier draft of this fix did exactly that) silently drifts
 *    out of alignment the moment any replacement up to that point has a
 *    different length than its original. There is no "offset in the
 *    masked text" that reliably corresponds to a particular offset in the
 *    demasked text.
 *
 * The approach that avoids both: never demask an isolated chunk. Instead,
 * always demask the prefix of everything-received-so-far that's "safe"
 * (far enough behind the live edge that no token inside it could still be
 * waiting on more characters — see SAFETY_MARGIN), which is monotonically
 * growing and therefore a stable, consistent prefix across calls. Emit
 * only the NEW tail of that result relative to what was already emitted
 * (`emittedRestoredLength`) — a plain diff against our own prior output,
 * never an offset borrowed from the masked side.
 *
 * No DOM, no chrome.* — `demask(text)` is injected so this runs identically
 * in the browser (MAIN world) and under Node for unit tests.
 */
(function (root, factory) {
  const lib = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = lib;
  } else {
    root.PIIGatewaySSEDemask = lib;
  }
})(typeof self !== "undefined" ? self : this, function () {
  const DEFAULT_FLUSH_THRESHOLD = 100;
  const DEFAULT_SAFETY_MARGIN = 80;

  /**
   * @param {ReadableStream<Uint8Array>} responseBody - the raw SSE response body
   * @param {(text: string) => Promise<{success: boolean, restoredText?: string}>} demask
   * @param {{flushThreshold?: number, safetyMargin?: number}} [options]
   * @returns {ReadableStream<Uint8Array>}
   */
  function createDemaskingStream(responseBody, demask, options = {}) {
    const flushThreshold = options.flushThreshold ?? DEFAULT_FLUSH_THRESHOLD;
    const safetyMargin = options.safetyMargin ?? DEFAULT_SAFETY_MARGIN;

    const reader = responseBody.getReader();
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();

    let lineBuffer = ""; // raw, not-yet-newline-terminated bytes from the wire
    let textBuffer = ""; // every masked text delta seen so far, concatenated
    let sinceLastFlush = 0;
    let emittedRestoredLength = 0; // how much of the *demasked* output we've already sent
    let lastTemplate = null; // {json, isV} — shape of the most recent text-bearing frame,
    // reused so the merged frame we emit still looks like a frame the
    // page's own SSE parser expects.

    function buildMergedFrame(text) {
      if (lastTemplate) {
        const clone = JSON.parse(JSON.stringify(lastTemplate.json));
        if (lastTemplate.isV) {
          if (clone.v?.message?.content) clone.v.message.content.parts = [text];
        } else if (clone.message?.content) {
          clone.message.content.parts = [text];
        }
        return `data: ${JSON.stringify(clone)}\n`;
      }
      return `data: ${JSON.stringify({ message: { content: { parts: [text] } } })}\n`;
    }

    // Demasks only the prefix of textBuffer that's safe to finalize, and
    // emits whatever's new in that result since the last emission. Safe to
    // call on every pull — it's a no-op unless there's enough new text (or
    // force is set, e.g. at stream end / before "[DONE]").
    async function maybeFlush(controller, { force = false } = {}) {
      if (!force && sinceLastFlush < flushThreshold) return;

      const safeLen = force ? textBuffer.length : Math.max(0, textBuffer.length - safetyMargin);
      if (safeLen <= 0) return;

      const prefix = textBuffer.slice(0, safeLen);
      const result = await demask(prefix);

      if (!result || !result.success || typeof result.restoredText !== "string") {
        // Demask failed. Non-forced: just retry on the next flush once
        // more text (and hopefully a working backend) arrives — nothing
        // un-demasked gets emitted in the meantime. Forced (stream
        // ending): we can't hold the stream open forever waiting for a
        // backend that may be down, so fall back to the masked prefix
        // itself — fail-open, but ONLY on the response side, and only
        // once there's truly nothing left to wait for. The request body
        // sent to the LLM was still masked either way.
        if (!force) {
          sinceLastFlush = 0;
          return;
        }
        if (prefix.length > emittedRestoredLength) {
          controller.enqueue(encoder.encode(buildMergedFrame(prefix.slice(emittedRestoredLength))));
          emittedRestoredLength = prefix.length;
        }
        sinceLastFlush = 0;
        return;
      }

      const restored = result.restoredText;
      sinceLastFlush = 0;
      if (restored.length <= emittedRestoredLength) return; // nothing new

      controller.enqueue(encoder.encode(buildMergedFrame(restored.slice(emittedRestoredLength))));
      emittedRestoredLength = restored.length;
    }

    return new ReadableStream({
      async pull(controller) {
        try {
          const { done, value } = await reader.read();

          if (done) {
            await maybeFlush(controller, { force: true });
            controller.close();
            return;
          }

          lineBuffer += decoder.decode(value, { stream: true });
          const lines = lineBuffer.split("\n");
          lineBuffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const data = line.slice(6).trim();

              if (data === "[DONE]") {
                // Terminal marker — no more text is coming after this, so
                // flush everything first. Otherwise the page could see
                // "[DONE]" (and stop its loading indicator) before the
                // last held-back characters of the reply ever arrive.
                await maybeFlush(controller, { force: true });
                controller.enqueue(encoder.encode("data: [DONE]\n"));
                continue;
              }

              try {
                const json = JSON.parse(data);
                const isV = !!json.v;
                let text = "";
                if (isV && json.v.message?.content) {
                  text = json.v.message.content.parts?.[0] || "";
                } else if (json.message?.content) {
                  text = json.message.content.parts?.[0] || "";
                }

                if (text) {
                  textBuffer += text;
                  sinceLastFlush += text.length;
                  lastTemplate = { json, isV };
                } else {
                  // Structural/non-text frame — doesn't depend on demask
                  // state, safe to pass straight through.
                  controller.enqueue(encoder.encode(`data: ${data}\n`));
                }
              } catch {
                controller.enqueue(encoder.encode(`data: ${data}\n`));
              }
            } else {
              controller.enqueue(encoder.encode(line + "\n"));
            }
          }

          await maybeFlush(controller);
        } catch (err) {
          controller.error(err);
        }
      },
      cancel() {
        reader.cancel();
      },
    });
  }

  return { createDemaskingStream, DEFAULT_FLUSH_THRESHOLD, DEFAULT_SAFETY_MARGIN };
});
