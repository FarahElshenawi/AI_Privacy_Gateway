/**
 * Doppel — show real values in ChatGPT / Gemini replies.
 *
 * IMPORTANT: demasking is intentionally restricted to assistant/model messages.
 * User messages must remain masked in the page. We do this at the rendered-DOM
 * boundary rather than by trying to infer message ownership from timing.
 */

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
  '[data-turn="assistant"]',
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
  const fn = (text) => text.replace(re, (m) => map.get(m));
  /** Where each known fake sits in `text` (used when a fake is split across several text nodes). */
  fn.matches = (text) => {
    const out = [];
    const r = new RegExp(re.source, "g");
    for (let m = r.exec(text); m; m = r.exec(text)) {
      out.push({ start: m.index, end: m.index + m[0].length, real: map.get(m[0]) });
      if (m[0].length === 0) r.lastIndex++;
    }
    return out;
  };
  return fn;
}

const BLOCK_SELECTOR = "p,li,h1,h2,h3,h4,h5,h6,td,th,blockquote,pre,div";

/**
 * A reply can be drawn as several adjacent text nodes (one per word or token while it streams,
 * or a name split by a <span>), so no single node holds the whole fake. Join the text of one
 * block, find the fakes in the joined string, and write the real value back across the nodes.
 */
function applyAcrossBlock(block, doc, replacer, written) {
  if (!replacer || !replacer.matches || !block || !doc.createTreeWalker) return false;
  const nodes = [];
  for (const n of textNodes(block, doc)) {
    if (isAssistantTextNode(n) && !isEditable(n)) nodes.push(n);
  }
  if (nodes.length < 2) return false;
  const joined = nodes.map((n) => n.nodeValue).join("");
  const found = replacer.matches(joined);
  if (found.length === 0) return false;
  const starts = [], lens = [];
  let pos = 0;
  for (const n of nodes) { starts.push(pos); lens.push(n.nodeValue.length); pos += n.nodeValue.length; }
  let changed = false;
  for (const m of found.reverse()) {                 // last match first: earlier offsets stay valid
    let placed = false;
    for (let i = 0; i < nodes.length; i++) {
      const ns = starts[i], ne = ns + lens[i];
      if (ne <= m.start || ns >= m.end) continue;
      const v = nodes[i].nodeValue;
      const a = Math.max(m.start - ns, 0);
      const b = Math.min(m.end - ns, ne - ns);
      nodes[i].nodeValue = v.slice(0, a) + (placed ? "" : m.real) + v.slice(b);
      placed = true;
      changed = true;
      if (written) written.set(nodes[i], nodes[i].nodeValue);
    }
  }
  return changed;
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

/** Ancestor chain of a text node as tag names plus a few structural attributes (never text). */
function describePath(node) {
  const parts = [];
  let el = node.parentElement || null;
  for (let i = 0; el && i < 7; i++, el = el.parentElement) {
    let s = (el.tagName || "?").toLowerCase();
    for (const a of ["data-message-author-role", "data-turn", "data-testid", "role"]) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v) s += `[${a}=${String(v).slice(0, 32)}]`;
    }
    parts.push(s);
  }
  return parts.reverse().join(">");
}

/**
 * The mapping is asked for only while a reply is appearing, never on a timer:
 *   - once at start (to restore replies already on the page),
 *   - when the page shows activity inside an assistant message (a reply streaming in, or history
 *     loading), at most once per `gapMs`, plus one final ask after the activity settles,
 *   - when the tab becomes visible or the conversation changes.
 * An idle tab makes no requests. The user's own typing and the page's other changes are ignored.
 */
function createDemasker(doc, send, opts = {}) {
  const gapMs = Number.isFinite(opts.gapMs) ? opts.gapMs : 1500;
  const report = typeof opts.report === "function" ? opts.report : null;   // diagnostics: a count, never text
  let replacedTotal = 0;
  let reportTimer = null;
  let replacer = null;
  let entriesSeen = null;
  let version = null;
  let pending = null;
  let lastRefresh = -Infinity;
  const written = new WeakMap();

  function blockOf(n) {
    const el = n && (n.nodeType === 3 ? (n.parentElement || n.parentNode) : n);
    return el && el.closest ? (el.closest(BLOCK_SELECTOR) || el) : null;
  }

  function blockPass(blocks) {
    for (const b of blocks) {
      if (b && applyAcrossBlock(b, doc, replacer, written)) noteReplaced();
    }
  }

  function scan(root) {
    if (!replacer) return;
    const blocks = new Set();
    for (const n of textNodes(root, doc)) {
      if (isAssistantTextNode(n) && !isEditable(n)) {
        if (applyToTextNode(n, replacer, written)) noteReplaced();
        blocks.add(blockOf(n));
      }
    }
    blockPass(blocks);
  }

  /** Diagnostics only (tag names, a few structural attribute values, counts; never page text). */
  function probe() {
    let assistantNodes = 0, fakesInAssistant = 0, fakesElsewhere = 0;
    const fakes = (entriesSeen || []).map((e) => e.fake).filter(Boolean);
    const where = [];
    for (const n of textNodes(doc.documentElement || doc.body, doc)) {
      const inA = isAssistantTextNode(n);
      if (inA) assistantNodes++;
      if (fakes.some((f) => n.nodeValue.includes(f))) {
        if (inA) fakesInAssistant++; else fakesElsewhere++;
        if (where.length < 3) where.push(describePath(n));
      }
    }
    const roles = {};
    if (doc.querySelectorAll) {
      for (const el of doc.querySelectorAll("[data-message-author-role],[data-turn]")) {
        const k = el.getAttribute("data-message-author-role") || `turn:${el.getAttribute("data-turn")}`;
        roles[k] = (roles[k] || 0) + 1;
      }
    }
    const testids = [];
    if (doc.querySelectorAll) {
      for (const el of doc.querySelectorAll("[data-testid]")) {
        const v = String(el.getAttribute("data-testid")).slice(0, 40);
        if (!testids.includes(v) && testids.length < 12) testids.push(v);
      }
    }
    return { entries: fakes.length, assistantNodes, fakesInAssistant, fakesElsewhere, where, roles, testids, replaced: replacedTotal };
  }

  function noteReplaced() {
    replacedTotal++;
    if (!report || reportTimer) return;
    reportTimer = setTimeout(() => { reportTimer = null; report(replacedTotal, probe()); }, 1000);
  }

  const insideAssistant = (n) => {
    const el = n && (n.parentElement || n.parentNode || n);
    return !!(el && el.closest && el.closest(ASSISTANT_SELECTOR));
  };

  /** True when a batch of DOM changes touches an assistant message (and is not our own rewrite). */
  function touchesAssistant(records) {
    for (const r of records) {
      if (r.type === "characterData") {
        const own = written.get(r.target) === r.target.nodeValue;
        if (!own && insideAssistant(r.target)) return true;
      } else {
        if (insideAssistant(r.target)) return true;
        for (const added of r.addedNodes || []) {
          if (added.nodeType === 1 && ((added.matches && added.matches(ASSISTANT_SELECTOR)) ||
              (added.querySelector && added.querySelector(ASSISTANT_SELECTOR)))) return true;
          if (added.nodeType === 3 && insideAssistant(added)) return true;
        }
      }
    }
    return false;
  }

  function requestRefresh() {
    if (pending) return;                       // one is already scheduled
    const wait = Math.max(0, lastRefresh + gapMs - Date.now());
    pending = setTimeout(() => { pending = null; refresh(); }, wait);
  }

  function onMutations(records) {
    if (replacer) {
      const blocks = new Set();
      for (const r of records) {
        if (r.type === "characterData") {
          if (isAssistantTextNode(r.target) && !isEditable(r.target)) {
            if (applyToTextNode(r.target, replacer, written)) noteReplaced();
            blocks.add(blockOf(r.target));
          }
        } else {
          for (const added of r.addedNodes) scan(added);
          if (insideAssistant(r.target)) blocks.add(blockOf(r.target));
        }
      }
      blockPass(blocks);
    }
    if (touchesAssistant(records)) requestRefresh();
  }

  const observer = typeof MutationObserver !== "undefined" ? new MutationObserver(onMutations) : null;

  async function refresh() {
    lastRefresh = Date.now();
    let res;
    try {
      res = await send({ type: "GET_MAPPING", sinceVersion: version });
    } catch {
      return false;
    }

    if (!res || !res.success) return false;

    if (res.enabled === false) {
      replacer = null;
      version = null;
      return true;
    }

    if (res.changed) {
      replacer = buildReplacer(res.entries);
      entriesSeen = res.entries;
      version = res.version;
      scan(doc.documentElement || doc.body);
    } else if (typeof res.version === "number") {
      version = res.version;
    }
    if (report) report(replacedTotal, probe());
    return true;
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

    // Restore a reply that is already on the page. The worker may still be waking up: try a few times.
    const first = (left) => refresh().then((ok) => { if (!ok && left > 0) setTimeout(() => first(left - 1), 1500); });
    first(3);

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
    _onMutations: onMutations,
    _state: () => ({ replacer, version }),
    _probe: probe,
    _stop: () => { clearTimeout(pending); pending = null; clearTimeout(reportTimer); reportTimer = null; }
  };
}

if (
  typeof document !== "undefined" &&
  typeof chrome !== "undefined" &&
  chrome.runtime &&
  chrome.runtime.sendMessage
) {
  const d = createDemasker(document, (m) => chrome.runtime.sendMessage(m), {
    report: (n, probe) => { try { chrome.runtime.sendMessage({ type: "DEMASK_REPORT", replaced: n, probe }); } catch { /* worker asleep */ } },
  });
  d.start();
  if (chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((m, _sender, reply) => {
      if (m && m.type === "DOPPEL_PROBE") {
        // Ask for the mapping first, so the report can say where each fake sits on this page.
        d.refresh().catch(() => {}).then(() => { try { reply({ success: true, probe: d._probe() }); } catch { reply({ success: false }); } });
        return true;
      }
      return false;
    });
  }

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
    applyAcrossBlock,
    textNodes,
    createDemasker
  };
}
