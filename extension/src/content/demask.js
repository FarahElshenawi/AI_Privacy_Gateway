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
 *   [data-turn="assistant"] (new renderer)
 *   .agent-turn (streaming/markdown turn)
 *
 * Gemini:
 *   model-response is the stable custom element used for rendered model replies.
 *   message-content is the rendered text container inside it (often in shadow DOM).
 *   The data-message-author-role variants are included for alternate/new renderers.
 *
 * Keep these selectors narrowly scoped: a false positive here would expose a
 * real value in a user-authored message, which is worse than failing to demask.
 * USER_SELECTOR always wins over ASSISTANT_SELECTOR.
 */
const ASSISTANT_SELECTOR = [
  '[data-message-author-role="assistant"]',
  '[data-message-author-role="model"]',
  '[data-turn="assistant"]',
  'model-response',
  'message-content',
  '.agent-turn',
  '[data-testid*="assistant"]',
  '[data-testid*="model"]'
].join(",");

/** Containers that are always user-authored: never demask inside these. */
const USER_SELECTOR = [
  '[data-message-author-role="user"]',
  '[data-turn="user"]',
  'user-query',
  '.user-turn',
  '[data-testid*="user"]'
].join(",");

/** closest() that also walks up through shadow hosts (Gemini renders in shadow DOM). */
function closestAcrossShadows(startEl, selector) {
  let cur = startEl || null;
  while (cur) {
    try {
      if (cur.closest) {
        const found = cur.closest(selector);
        if (found) return found;
      }
    } catch {
      return null; // invalid selector: treat as no match
    }
    let host = null;
    try {
      host = (cur.getRootNode && cur.getRootNode().host) || null;
    } catch {
      host = null;
    }
    if (!host || host === cur) return null;
    cur = host;
  }
  return null;
}

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
 *
 * Matches are applied rightmost-first, one per iteration with recomputed offsets, so two
 * fakes in the same block cannot corrupt each other's positions.
 */
function applyAcrossBlock(block, doc, replacer, written) {
  if (!replacer || !replacer.matches || !block) return false;
  const nodes = [];
  for (const n of textNodes(block, doc)) {
    if (isAssistantTextNode(n) && !isEditable(n)) nodes.push(n);
  }
  if (nodes.length < 2) return false;
  let changed = false;
  for (let iter = 0; iter < 10; iter++) {
    const starts = [], lens = [];
    let pos = 0;
    for (const n of nodes) {
      starts.push(pos);
      const len = (n.nodeValue || "").length;
      lens.push(len);
      pos += len;
    }
    const joined = nodes.map((n) => n.nodeValue).join("");
    const found = replacer.matches(joined);
    if (found.length === 0) break;
    const m = found[found.length - 1]; // rightmost: earlier offsets stay valid
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
    if (!placed) break;
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
  if (!el) return false;
  return !!closestAcrossShadows(el, SKIP_SELECTOR);
}

/**
 * Only text belonging to an assistant/model message may be demasked.
 * User containers always win: even if a broad assistant selector matches,
 * text inside a user message is never touched.
 *
 * This is deliberately fail-closed:
 * if a site changes its DOM and we cannot identify the assistant container,
 * we leave the fake value visible rather than risking exposure in a user message.
 */
function isAssistantTextNode(node) {
  const el = node.parentElement || node.parentNode;
  if (!el) return false;
  if (closestAcrossShadows(el, USER_SELECTOR)) return false;
  return !!closestAcrossShadows(el, ASSISTANT_SELECTOR);
}

/**
 * Visit every text node under root, piercing shadow roots (Gemini renders
 * model replies inside shadow DOM, which TreeWalker alone never enters).
 * Falls back to TreeWalker for test doubles without childNodes.
 */
function* textNodes(root, doc) {
  if (!root) return;
  if (root.nodeType === 3) {
    yield root;
    return;
  }
  if (!root.childNodes || typeof root.childNodes.length !== "number") {
    if (!doc || !doc.createTreeWalker) return;
    const w = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
    for (let n = w.nextNode(); n; n = w.nextNode()) yield n;
    if (root.shadowRoot) yield* textNodes(root.shadowRoot, doc);
    return;
  }
  const stack = [];
  const pushKids = (el) => {
    const kids = el.childNodes;
    if (!kids) return;
    for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
  };
  if (root.shadowRoot) pushKids(root.shadowRoot);
  pushKids(root);
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    if (n.nodeType === 3) {
      yield n;
    } else if (n.nodeType === 1 || n.nodeType === 11) {
      if (n.shadowRoot) {
        const skids = n.shadowRoot.childNodes;
        if (skids) for (let i = skids.length - 1; i >= 0; i--) stack.push(skids[i]);
      }
      pushKids(n);
    }
  }
}

/** All elements under root, piercing shadow roots (for content-free diagnostics). */
function* elementsDeep(root) {
  if (!root) return;
  const stack = [root];
  const seen = new Set();
  while (stack.length) {
    const n = stack.pop();
    if (!n || seen.has(n)) continue;
    seen.add(n);
    if (n.nodeType === 1) yield n;
    let kids = null;
    try { kids = n.childNodes; } catch { kids = null; }
    if (kids) for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
    let shadow = null;
    try { shadow = n.shadowRoot || null; } catch { shadow = null; }
    if (shadow) {
      try {
        const skids = shadow.childNodes;
        if (skids) for (let i = skids.length - 1; i >= 0; i--) stack.push(skids[i]);
        else stack.push(shadow);
      } catch { /* ignore */ }
    }
  }
}

/** Ancestor chain of a text node as tag names plus a few structural attributes (never text). */
function describePath(node) {
  const parts = [];
  let el = node.parentElement || null;
  const seenHosts = new Set();
  for (let i = 0; el && i < 10; i++) {
    let s = "?";
    try { s = (el.tagName || "?").toLowerCase(); } catch { s = "?"; }
    for (const a of ["data-message-author-role", "data-turn", "data-testid", "role", "class"]) {
      let v = null;
      try { v = el.getAttribute && el.getAttribute(a); } catch { v = null; }
      if (v) s += `[${a}=${String(v).slice(0, 32)}]`;
    }
    parts.push(s);
    let next = null;
    try { next = el.parentElement || null; } catch { next = null; }
    if (!next) {
      try {
        const host = el.getRootNode && el.getRootNode().host;
        if (host && !seenHosts.has(host)) { seenHosts.add(host); next = host; }
      } catch { next = null; }
    }
    el = next;
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
    if (!el) return null;
    try {
      return closestAcrossShadows(el, BLOCK_SELECTOR) || el;
    } catch {
      return el;
    }
  }

  function blockPass(blocks) {
    for (const b of blocks) {
      try {
        if (b && applyAcrossBlock(b, doc, replacer, written)) noteReplaced();
      } catch { /* one bad block must not stop the others */ }
    }
  }

  function scan(root) {
    if (!replacer || !root) return;
    const blocks = new Set();
    try {
      for (const n of textNodes(root, doc)) {
        if (isAssistantTextNode(n) && !isEditable(n)) {
          if (applyToTextNode(n, replacer, written)) noteReplaced();
          blocks.add(blockOf(n));
        }
      }
    } catch { /* a detached root must not break refresh */ }
    blockPass(blocks);
  }

  /** Diagnostics only (tag names, a few structural attribute values, counts; never page text). */
  function probe() {
    try {
      const root = doc.documentElement || doc.body;
      if (!root) return { entries: 0, assistantNodes: 0, fakesInAssistant: 0, fakesElsewhere: 0, where: [], roles: {}, testids: [], replaced: replacedTotal };
      let assistantNodes = 0, fakesInAssistant = 0, fakesElsewhere = 0;
      const fakes = (entriesSeen || []).map((e) => e.fake).filter(Boolean);
      const where = [];
      for (const n of textNodes(root, doc)) {
        let inA = false;
        try { inA = isAssistantTextNode(n); } catch { inA = false; }
        if (inA) assistantNodes++;
        try {
          if (fakes.length && fakes.some((f) => (n.nodeValue || "").includes(f))) {
            if (inA) fakesInAssistant++; else fakesElsewhere++;
            if (where.length < 3) where.push(describePath(n));
          }
        } catch { /* ignore one bad node */ }
      }
      const roles = {};
      const testids = [];
      const hasRealDom = !!(doc.querySelectorAll && root.childNodes && typeof root.childNodes.length === "number");
      if (hasRealDom) {
        for (const el of elementsDeep(root)) {
          try {
            const role = el.getAttribute && el.getAttribute("data-message-author-role");
            const turn = el.getAttribute && el.getAttribute("data-turn");
            if (role !== null && role !== undefined || turn !== null && turn !== undefined) {
              const k = role || `turn:${turn}`;
              roles[k] = (roles[k] || 0) + 1;
            }
            const tid = el.getAttribute && el.getAttribute("data-testid");
            if (tid) {
              const v = String(tid).slice(0, 40);
              if (!testids.includes(v) && testids.length < 12) testids.push(v);
            }
          } catch { /* ignore */ }
        }
      } else {
        if (doc.querySelectorAll) {
          for (const el of doc.querySelectorAll("[data-message-author-role],[data-turn]")) {
            const k = el.getAttribute("data-message-author-role") || `turn:${el.getAttribute("data-turn")}`;
            roles[k] = (roles[k] || 0) + 1;
          }
        }
        if (doc.querySelectorAll) {
          for (const el of doc.querySelectorAll("[data-testid]")) {
            const v = String(el.getAttribute("data-testid")).slice(0, 40);
            if (!testids.includes(v) && testids.length < 12) testids.push(v);
          }
        }
      }
      return { entries: fakes.length, assistantNodes, fakesInAssistant, fakesElsewhere, where, roles, testids, replaced: replacedTotal };
    } catch {
      return { entries: 0, assistantNodes: 0, fakesInAssistant: 0, fakesElsewhere: 0, where: [], roles: {}, testids: [], replaced: replacedTotal };
    }
  }

  function noteReplaced() {
    replacedTotal++;
    if (!report || reportTimer) return;
    reportTimer = setTimeout(() => { reportTimer = null; report(replacedTotal, probe()); }, 1000);
  }

  const insideAssistant = (n) => {
    if (!n) return false;
    const el = n.nodeType === 3 ? (n.parentElement || n.parentNode) : (n.nodeType === 1 ? n : (n.parentElement || n.parentNode || n));
    if (!el) return false;
    try {
      if (closestAcrossShadows(el, USER_SELECTOR)) return false;
      if (el.matches) {
        try {
          if (el.matches(USER_SELECTOR)) return false;
          if (el.matches(ASSISTANT_SELECTOR)) return true;
        } catch { /* invalid selector in this DOM: fall through */ }
      }
      return !!closestAcrossShadows(el, ASSISTANT_SELECTOR);
    } catch {
      return false;
    }
  };

  /** True when an added element (or its shadow/light descendants) holds an assistant container. */
  function subtreeHasAssistant(el) {
    if (!el || el.nodeType !== 1) return false;
    try {
      if (el.matches) {
        try {
          if (el.matches(USER_SELECTOR)) return false;
          if (el.matches(ASSISTANT_SELECTOR)) return true;
        } catch { /* ignore */ }
      }
      if (el.querySelector) {
        try { if (el.querySelector(ASSISTANT_SELECTOR)) return true; } catch { /* ignore */ }
      }
      let shadow = null;
      try { shadow = el.shadowRoot || null; } catch { shadow = null; }
      if (shadow) {
        try {
          if (shadow.querySelector && shadow.querySelector(ASSISTANT_SELECTOR)) return true;
        } catch { /* ignore */ }
        try {
          const kids = shadow.childNodes;
          if (kids) for (const k of kids) {
            if (k && k.nodeType === 1 && subtreeHasAssistant(k)) return true;
          }
        } catch { /* ignore */ }
      }
    } catch {
      return false;
    }
    return false;
  }

  /** True when a batch of DOM changes touches an assistant message (and is not our own rewrite). */
  function touchesAssistant(records) {
    for (const r of records) {
      if (r.type === "characterData") {
        let own = false;
        try { own = written.get(r.target) === r.target.nodeValue; } catch { own = false; }
        if (!own && insideAssistant(r.target)) return true;
      } else {
        if (insideAssistant(r.target)) return true;
        for (const added of r.addedNodes || []) {
          if (!added) continue;
          if (added.nodeType === 1 && subtreeHasAssistant(added)) return true;
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
    if (!Array.isArray(records)) return;
    if (replacer) {
      const blocks = new Set();
      for (const r of records) {
        if (!r) continue;
        if (r.type === "characterData") {
          try {
            if (isAssistantTextNode(r.target) && !isEditable(r.target)) {
              if (applyToTextNode(r.target, replacer, written)) noteReplaced();
              blocks.add(blockOf(r.target));
            }
          } catch { /* ignore one bad record */ }
        } else {
          try {
            for (const added of r.addedNodes || []) scan(added);
          } catch { /* ignore */ }
          try {
            if (insideAssistant(r.target)) blocks.add(blockOf(r.target));
          } catch { /* ignore */ }
        }
      }
      blockPass(blocks);
    }
    try {
      if (touchesAssistant(records)) requestRefresh();
    } catch { /* never break the page on diagnostics */ }
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
      entriesSeen = null;
      version = null;
      return true;
    }

    if (res.changed) {
      replacer = buildReplacer(res.entries);
      entriesSeen = res.entries;
      version = res.version;
      try {
        scan(doc.documentElement || doc.body);
      } catch { /* a half-loaded DOM must not fail the refresh */ }
    } else if (typeof res.version === "number") {
      version = res.version;
    }
    if (report) {
      try { report(replacedTotal, probe()); } catch { /* diagnostics never fail refresh */ }
    }
    return true;
  }

  function start() {
    if (observer) {
      const attach = () => {
        try {
          const root = doc.documentElement;
          if (!root) return;
          observer.observe(root, {
            childList: true,
            subtree: true,
            characterData: true
          });
        } catch { /* page without observable root: first() still restores */ }
      };

      if (doc.documentElement) attach();
      else if (doc.addEventListener) doc.addEventListener("DOMContentLoaded", attach, { once: true });
    }

    // Restore a reply that is already on the page. The worker may still be waking up: try a few times.
    const first = (left) => {
      try {
        return refresh().then((ok) => { if (!ok && left > 0) setTimeout(() => first(left - 1), 1500); }).catch(() => {
          if (left > 0) setTimeout(() => first(left - 1), 1500);
        });
      } catch {
        if (left > 0) setTimeout(() => first(left - 1), 1500);
        return Promise.resolve(false);
      }
    };
    first(3);

    if (doc.addEventListener) doc.addEventListener("visibilitychange", () => {
      try { if (!doc.hidden) refresh(); } catch { /* ignore */ }
    });
  }

  function reset() {
    replacer = null;
    entriesSeen = null;
    version = null;
    try { refresh().catch(() => {}); } catch { /* ignore */ }
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
    elementsDeep,
    closestAcrossShadows,
    createDemasker
  };
}
