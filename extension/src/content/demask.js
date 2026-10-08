/**
 * Doppel — show real values in ChatGPT / Gemini replies.
 *
 * The model only ever saw fake values (see the masking side), so its answers contain them.
 * This script restores the real values in what the USER sees, as the text streams in:
 *
 *   - It asks the extension service worker for this conversation's fake→real entries
 *     (the worker gets them from the local backend; this page never talks to the backend).
 *   - A MutationObserver rewrites text nodes that contain a known fake. Because it works on the
 *     rendered text, streaming keeps working and so do reloaded chat histories (the server keeps
 *     the fake text; it is restored again every time it is rendered).
 *
 * Honest limits: the real values are written into the page's DOM, so the site's own scripts can
 * technically read them (the same is true of anything the user types). Replacement is
 * longest-fake-first with word boundaries, the same rule as the backend's /api/demask.
 * Turning the popup switch off stops NEW replacements; reload the page to see fakes again.
 */

const POLL_MS = 2000;
const SKIP_SELECTOR = "script,style,textarea,input,[contenteditable='true'],[contenteditable='']";

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
    .sort((a, b) => b.length - a.length)               // longest first
    .map((f) => (word(f[0]) ? "(?<![A-Za-z0-9_])" : "") + escape(f) + (word(f[f.length - 1]) ? "(?![A-Za-z0-9_])" : ""));
  const re = new RegExp(alts.join("|"), "g");
  return (text) => text.replace(re, (m) => map.get(m));
}

/** Rewrite one text node in place. Returns true if it changed. */
function applyToTextNode(node, replacer, written) {
  const text = node.nodeValue;
  if (!text || (written && written.get(node) === text)) return false;   // our own output
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

/** Visit every text node under `root` (a TreeWalker in the browser). */
function* textNodes(root, doc) {
  if (root.nodeType === 3) { yield root; return; }
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
      if (!isEditable(n)) applyToTextNode(n, replacer, written);
    }
  }

  const observer = typeof MutationObserver !== "undefined" ? new MutationObserver((records) => {
    if (!replacer) return;
    for (const r of records) {
      if (r.type === "characterData") {
        if (!isEditable(r.target)) applyToTextNode(r.target, replacer, written);
      } else {
        for (const added of r.addedNodes) scan(added);
      }
    }
  }) : null;

  async function refresh() {
    let res;
    try {
      res = await send({ type: "GET_MAPPING", sinceVersion: version });
    } catch { return; }                              // worker asleep/backend down: keep what we have
    if (!res || !res.success) return;
    if (res.enabled === false) { replacer = null; version = null; return; }
    if (res.changed) {
      replacer = buildReplacer(res.entries);
      version = res.version;
      scan(doc.documentElement || doc.body);         // restore text that is already on screen
    } else if (typeof res.version === "number") {
      version = res.version;
    }
  }

  function start() {
    if (observer) {
      const attach = () => observer.observe(doc.documentElement, { childList: true, subtree: true, characterData: true });
      if (doc.documentElement) attach(); else doc.addEventListener("DOMContentLoaded", attach, { once: true });
    }
    refresh();
    timer = setInterval(() => { if (!doc.hidden) refresh(); }, POLL_MS);
    doc.addEventListener("visibilitychange", () => { if (!doc.hidden) refresh(); });
  }

  // A different conversation (SPA navigation) has a different vault: start from scratch.
  function reset() { replacer = null; version = null; refresh(); }

  return { start, refresh, reset, scan, _state: () => ({ replacer, version }), _stop: () => clearInterval(timer) };
}

if (typeof document !== "undefined" && typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
  const d = createDemasker(document, (m) => chrome.runtime.sendMessage(m));
  d.start();
  let lastPath = location.pathname;
  setInterval(() => { if (location.pathname !== lastPath) { lastPath = location.pathname; d.reset(); } }, 500);
}

// Test hook (Node): the content script is a classic script in the browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { buildReplacer, applyToTextNode, isEditable, textNodes, createDemasker };
}
