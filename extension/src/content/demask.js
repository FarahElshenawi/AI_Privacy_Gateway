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

  // Cache for debugging: so applyAcrossBlock can log what fakes it's trying to match
  replacerMatchesCache = map;

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
    if (!isEditable(n)) nodes.push(n);
  }
  if (nodes.length < 2) return false;
  // Use \x00 as separator: cannot appear in real text or fakes; the regex word boundaries
  // treat it as a non-word char, so fakes never match across it. But we also try a direct
  // join (no separator) because some renderers put the space inside one of the text nodes.
  const joined = nodes.map((n) => n.nodeValue).join("");
  const found = replacer.matches(joined);
  if (found.length === 0) {
    // Debug: show what we're looking at when nothing is found
    if (nodes.length >= 2 && nodes.length <= 6) {
      const sample = nodes.map((n) => `"${n.nodeValue}"`).join(", ");
      const fakes = [...replacerMatchesCache.keys()].slice(0, 3);
      console.log(`[Doppel demask] applyAcrossBlock: ${nodes.length} nodes [${sample}] — no match for fakes [${fakes}]`);
    }
    return false;
  }
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

// Hack: expose the replacer's fake keys for debugging. buildReplacer stores them in the closure;
// we can't access them directly, so we cache the last-built fake list here.
let replacerMatchesCache = new Map();

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
 * Is this text node inside an assistant/model message container?
 * Used for DIAGNOSTICS only — demasking itself no longer gates on this (see scan()).
 *
 * Why demask all non-editable text, not just assistant replies?
 *   - ChatGPT renders user messages from the server response, which carries the MASKED
 *     text (the extension masked it before sending). So user messages in the DOM contain
 *     fake values too — especially on reloaded chat history.
 *   - When "Show real values" is ON, the user wants real values everywhere.
 *   - The composer is excluded by isEditable(), so we never touch text being typed.
 *   - The only risk: a user message coincidentally containing text that matches a fake.
 *     Fakes are generated names/emails/phones — collision is negligible.
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
    let scanned = 0, replaced = 0;
    const missed = [];  // text nodes that might contain a fake but weren't replaced
    for (const n of textNodes(root, doc)) {
      if (isEditable(n)) continue;       // never touch the composer or any input
      scanned++;
      if (applyToTextNode(n, replacer, written)) { noteReplaced(); replaced++; }
      else {
        // Check if this node's text contains any fake substring (even partial)
        const t = n.nodeValue || "";
        if (t.length > 0) {
          for (const fake of replacerMatchesCache.keys()) {
            // Check if any word of the fake appears in this node
            const words = fake.split(/\s+/);
            if (words.some((w) => w.length > 2 && t.includes(w))) {
              missed.push(`"${t.slice(0, 60)}" (matched word from "${fake.slice(0, 30)}")`);
              break;
            }
          }
        }
      }
      blocks.add(blockOf(n));
    }
    if (scanned > 0) {
      console.log(`[Doppel demask] scan: ${scanned} text nodes, ${replaced} replaced`);
      if (replaced === 0 && missed.length > 0) {
        console.log(`[Doppel demask] ${missed.length} nodes contain fake words but weren't replaced (likely split across nodes):`);
        missed.slice(0, 5).forEach((m) => console.log(`  → ${m}`));
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

  /** True when a batch of DOM changes touches non-editable text (and is not our own rewrite).
   *  Used to decide whether to request a fresh mapping. Any non-editable text change could
   *  contain a fake that needs restoring — we don't gate on assistant-only selectors anymore. */
  function touchesAssistant(records) {
    for (const r of records) {
      if (r.type === "characterData") {
        const own = written.get(r.target) === r.target.nodeValue;
        if (!own && !isEditable(r.target)) return true;
      } else {
        if (!isEditable(r.target)) return true;
        for (const added of r.addedNodes || []) {
          if (added.nodeType === 3 && !isEditable(added)) return true;
          if (added.nodeType === 1) return true;  // a new element: scan will check its text nodes
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
          if (!isEditable(r.target)) {
            if (applyToTextNode(r.target, replacer, written)) noteReplaced();
            blocks.add(blockOf(r.target));
          }
        } else {
          for (const added of r.addedNodes) scan(added);
          if (!isEditable(r.target)) blocks.add(blockOf(r.target));
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
    } catch (e) {
      console.log("[Doppel demask] GET_MAPPING failed (worker asleep?)", e?.message || e);
      return false;
    }

    console.log("[Doppel demask] GET_MAPPING response:", JSON.stringify({
      success: res?.success, enabled: res?.enabled, changed: res?.changed,
      version: res?.version, entryCount: res?.entries?.length || 0,
      firstFake: res?.entries?.[0]?.fake?.slice(0, 30)
    }));

    if (!res || !res.success) {
      console.log("[Doppel demask] GET_MAPPING returned failure", res);
      return false;
    }

    if (res.enabled === false) {
      console.log("[Doppel demask] demask is disabled (toggle is OFF)");
      replacer = null;
      version = null;
      return true;
    }

    if (res.changed) {
      replacer = buildReplacer(res.entries);
      entriesSeen = res.entries;
      version = res.version;
      if (replacer) {
        const fakes = res.entries.map((e) => e.fake).slice(0, 5).join(", ");
        console.log(`[Doppel demask] mapping changed: ${res.entries.length} entries, version ${res.version}. Fakes: [${fakes}${res.entries.length > 5 ? "..." : ""}]`);
      } else {
        console.log(`[Doppel demask] mapping changed: ${res.entries.length} entries but buildReplacer returned null (empty/invalid entries)`);
      }
      scan(doc.documentElement || doc.body);
    } else {
      if (typeof res.version === "number") version = res.version;
      if (replacer) scan(doc.documentElement || doc.body);
    }
    if (report) report(replacedTotal, probe());
    return true;
  }

  let pollTimer = null;
  const POLL_MS = 2000;  // Safety-net poll (same as the original working version, commit a8376ea).
                         // Catches fakes the mutation observer missed: React reconciliation,
                         // short replies that finish before the gap, selector changes, and the
                         // user flipping the toggle on a static page. The SW short-circuits when
                         // the toggle is off; the backend short-circuits when the vault version
                         // is unchanged (since_version matches). Uses /api/mapping (NOT /api/demask,
                         // which has a 100-calls/10-min rate limit that a 2s poll would exceed).

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

    // Restore a reply that is already on the page. The worker may still be waking up: try a few
    // times. Retry on ANY outcome (including enabled:false / empty entries) so we pick up a
    // toggle that was flipped just after load, or a vault that gets its first entries a moment
    // later.
    const first = (left) => refresh().then(() => { if (left > 0) setTimeout(() => first(left - 1), 1500); });
    first(3);

    doc.addEventListener("visibilitychange", () => {
      if (!doc.hidden) refresh();
    });

    // Safety-net poll: every POLL_MS while the tab is visible. This is the backstop that
    // commit 5697d11 removed (causing the demasking regression). Without it, several real
    // scenarios never trigger a refresh: a short reply that finishes before the mutation gap,
    // a React virtual DOM reconciliation that doesn't fire characterData mutations on the text
    // nodes we watch, a ChatGPT DOM change that breaks touchesAssistant(), or the user flipping
    // the toggle on a page whose reply already finished. The poll catches all of them within
    // POLL_MS. Uses /api/mapping (cheap, versioned, rate-limit-friendly).
    pollTimer = setInterval(() => { if (!doc.hidden) refresh(); }, POLL_MS);
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
    _stop: () => { clearTimeout(pending); pending = null; clearTimeout(reportTimer); reportTimer = null; clearInterval(pollTimer); pollTimer = null; }
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

  // The popup toggles `demaskEnabled` in chrome.storage.local; the page is otherwise idle (no
  // mutations, no visibility change) when the user flips the switch on a chat whose replies are
  // already on screen. Without this listener, those existing replies would never be demasked —
  // refresh() ran once at load (when the toggle was still off → enabled:false → retry chain
  // stopped) and the only other triggers are mutations, visibility, and URL path changes.
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.demaskEnabled) {
        d.refresh();
      }
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
