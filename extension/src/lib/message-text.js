/**
 * Pure helpers for reading/rewriting ChatGPT's conversation request body.
 * No DOM, no chrome.*, no network — safe to run in the browser (MAIN
 * world, loaded as a plain global) or under Node for unit tests.
 */
(function (root, factory) {
  const lib = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = lib;
  } else {
    root.PIIGatewayMessageText = lib;
  }
})(typeof self !== "undefined" ? self : this, function () {
  function matchesPattern(url, patterns) {
    return patterns.some((p) => url.includes(p));
  }

  // ChatGPT's request format: { messages: [{ ..., content: { parts: [...] } }] }
  //
  // Where the user's role lives has moved at least once in the wild: an
  // older shape put it directly on the message (`msg.role === "user"`),
  // which is what this function originally checked. A live request
  // payload captured 2026-10-02 showed the current shape nests it one
  // level deeper instead — `msg.author.role === "user"`, with `msg.role`
  // absent entirely. Since `msg.role` was always undefined against that
  // shape, `msg.role === "user"` was always false, extractMessageText()
  // always returned null, and fetch-override.js's early-return on a
  // null/empty originalText (`if (!originalText) return next(...)`)
  // meantEVERY conversation request silently passed through completely
  // unmasked, with no error and no log line — the extension looked
  // fully functional (loaded, no exceptions) while doing nothing at all.
  // Checking both locations (new shape first, old shape as a fallback)
  // means a future reversion — or ChatGPT running an A/B test where some
  // sessions get one shape and some the other — degrades gracefully
  // instead of going silently blind again.
  function _getRole(msg) {
    if (msg.author && typeof msg.author.role === "string") return msg.author.role;
    return msg.role;
  }

  function extractMessageText(body) {
    try {
      const parsed = JSON.parse(body);
      if (parsed.messages && Array.isArray(parsed.messages)) {
        const texts = [];
        for (const msg of parsed.messages) {
          if (_getRole(msg) === "user" && msg.content && msg.content.parts) {
            for (const part of msg.content.parts) {
              if (typeof part === "string") texts.push(part);
            }
          }
        }
        return texts.join("\n");
      }
      return null;
    } catch {
      return null;
    }
  }

  function replaceMessageText(body, originalText, maskedText) {
    try {
      const parsed = JSON.parse(body);
      if (parsed.messages && Array.isArray(parsed.messages)) {
        for (const msg of parsed.messages) {
          if (_getRole(msg) === "user" && msg.content && msg.content.parts) {
            for (let i = 0; i < msg.content.parts.length; i++) {
              if (typeof msg.content.parts[i] === "string") {
                msg.content.parts[i] = msg.content.parts[i].replace(originalText, maskedText);
              }
            }
          }
        }
      }
      return JSON.stringify(parsed);
    } catch {
      return body;
    }
  }

  return { matchesPattern, extractMessageText, replaceMessageText };
});
