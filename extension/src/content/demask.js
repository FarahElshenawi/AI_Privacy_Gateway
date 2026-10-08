/**
 * Doppel — show real values in ChatGPT / Gemini replies.
 *
 * IMPORTANT: demasking is intentionally restricted to assistant/model messages.
 * User messages must remain masked in the page. We do this at the rendered-DOM
 * boundary rather than by trying to infer message ownership from timing.
 */

const POLL_MS = 2000;
const SKIP_SELECTOR = "script,style,textarea,input,[contenteditable='true'],[contenteditable='']";

/**
 * Containers that represent model/assistant messages.
 *
 * ChatGPT:
 *   [data-message-author-role="assistant"]
 *
 * Gemini:
 *   model-response is the stable custom element used for rendered model replies.
 *   The data-message-author-role variants are included for alternate/new renderers.
 *
 * Keep these selectors narrowly scoped: a false positive here would expose a
 * real value in a user-authored message, which is worse than failing to demask.
 */
const ASSISTANT_SELECTOR = [
  '[data-message-author-role="assistant"]',
  '[data-message-author-role="model"]',
  'model-response'
].join(",");

/** entries: [{fake, real}] → fn(text) → text with every known fake replaced. */
function buildReplacer(entries) {
  const map = new Map();
  for (const e of entries || []) {
    if (e && typeof e.fake === "string" && e.fake.length > 0 && typeof e.real === "string") {
      map.set(e.fake, e.real);
    }
  }
  if (map.size === 0) return null;

  const word = (ch) => /[A-Za-z0-9_]/.test(ch);
  const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const alts = [...map.keys()]
    .sort((a, b) => b.length - a.length)
    .map((f) =>
      (word(f[0]) ? "(?<![A-Za-z0-9_])" : "") +
      escape(f) +
      (word(f[f.length - 1]) ? "(?![A-Za-z0-9_])" : "")
    );

  const re = new RegExp(alts.join("|"), "g");
  return (text) => text.replace(re, (m) => map.get(m));
}

/** Rewrite one text node in place. Returns true if it changed. */
function applyToTextNode(node, replacer, written) {
  const text = node.nodeValue;
  if (!text || (written && written.get(node) === text)) return false;
  const out = replacer(text);
  if (out === text) return false;
  node.nodeValue = out;
  if (written) written.set(node, out);
  return true;
}

function isEditable(node) {
  const el = node.parentElement || node.parentNode;
  return !!(el && el.closest && el.closest(SKIP_SELECTOR));
}

/**
 * Only text belonging to an assistant/model message may be demasked.
 *
 * This is deliberately fail-closed:
 * if a site changes its DOM and we cannot identify the assistant container,
 * we leave the fake value visible rather than risking exposure in a user message.
 */
function isAssistantTextNode(node) {
  const el = node.parentElement || node.parentNode;
  return !!(el && el.closest && el.closest(ASSISTANT_SELECTOR));
}

/** Visit every text node under root. */
function* textNodes(root, doc) {
  if (root.nodeType === 3) {
    yield root;
    return;
  }
  const w = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  for (let n = w.nextNode(); n; n = w.nextNode()) yield n;
}

function createDemasker(doc, send) {
  let replacer = null;
  let version = null;
  let timer = null;
  const written = new WeakMap();

  function scan(root) {
    if (!replacer) return;
    for (const n of textNodes(root, doc)) {
      if (isAssistantTextNode(n) && !isEditable(n)) {
        applyToTextNode(n, replacer, written);
      }
    }
  }

  const observer = typeof MutationObserver !== "undefined"
    ? new MutationObserver((records) => {
        if (!replacer) return;

        for (const r of records) {
          if (r.type === "characterData") {
            if (isAssistantTextNode(r.target) && !isEditable(r.target)) {
              applyToTextNode(r.target, replacer, written);
            }
          } else {
            for (const added of r.addedNodes) scan(added);
          }
        }
      })
    : null;

  async function refresh() {
    let res;
    try {
      res = await send({ type: "GET_MAPPING", sinceVersion: version });
    } catch {
      return;
    }

    if (!res || !res.success) return;

    if (res.enabled === false) {
      replacer = null;
      version = null;
      return;
    }

    if (res.changed) {
      replacer = buildReplacer(res.entries);
      version = res.version;
      scan(doc.documentElement || doc.body);
    } else if (typeof res.version === "number") {
      version = res.version;
    }
  }

  function start() {
    if (observer) {
      const attach = () => observer.observe(doc.documentElement, {
        childList: true,
        subtree: true,
        characterData: true
      });

      if (doc.documentElement) attach();
      else doc.addEventListener("DOMContentLoaded", attach, { once: true });
    }

    refresh();
    timer = setInterval(() => {
      if (!doc.hidden) refresh();
    }, POLL_MS);

    doc.addEventListener("visibilitychange", () => {
      if (!doc.hidden) refresh();
    });
  }

  function reset() {
    replacer = null;
    version = null;
    refresh();
  }

  return {
    start,
    refresh,
    reset,
    scan,
    _state: () => ({ replacer, version }),
    _stop: () => clearInterval(timer)
  };
}

if (
  typeof document !== "undefined" &&
  typeof chrome !== "undefined" &&
  chrome.runtime &&
  chrome.runtime.sendMessage
) {
  const d = createDemasker(document, (m) => chrome.runtime.sendMessage(m));
  d.start();

  let lastPath = location.pathname;
  setInterval(() => {
    if (location.pathname !== lastPath) {
      lastPath = location.pathname;
      d.reset();
    }
  }, 500);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    buildReplacer,
    applyToTextNode,
    isEditable,
    isAssistantTextNode,
    textNodes,
    createDemasker
  };
}
