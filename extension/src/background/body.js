/**
 * Pure, DOM/chrome-free helpers for the service worker.
 * Kept separate so they can be unit-tested with `node --test`.
 */

// ── base64 / bytes ────────────────────────────────────────

export function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function concatBytes(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}

// ── message body parsing ──────────────────────────────────

export function getUserRole(msg) {
  if (!msg) return null;
  if (msg.role) return msg.role;
  if (msg.author && msg.author.role) return msg.author.role;
  return null;
}

/**
 * Collect every user-authored text string in a ChatGPT send body as
 * "slots": { get(): string, set(v): void }. Each part is masked on its own
 * and written back by assignment (no substring replace, no cross-part joins).
 */
export function collectUserTextSlots(parsed) {
  const slots = [];
  if (!parsed || !Array.isArray(parsed.messages)) return slots;
  for (const msg of parsed.messages) {
    if (getUserRole(msg) !== "user") continue;
    const content = msg && msg.content;
    if (!content) continue;
    if (Array.isArray(content.parts)) {
      content.parts.forEach((part, i) => {
        if (typeof part === "string") {
          slots.push({ get: () => content.parts[i], set: (v) => { content.parts[i] = v; } });
        } else if (part && typeof part === "object" && typeof part.text === "string") {
          slots.push({ get: () => part.text, set: (v) => { part.text = v; } });
        }
      });
    } else if (typeof content.text === "string") {
      slots.push({ get: () => content.text, set: (v) => { content.text = v; } });
    }
  }
  return slots;
}

/** Attachment descriptors on user messages (their `name` is a filename → PII risk). */
export function collectAttachmentNameSlots(parsed) {
  const slots = [];
  if (!parsed || !Array.isArray(parsed.messages)) return slots;
  for (const msg of parsed.messages) {
    if (getUserRole(msg) !== "user") continue;
    const atts = msg.metadata && msg.metadata.attachments;
    if (!Array.isArray(atts)) continue;
    for (const a of atts) {
      if (a && typeof a.name === "string") {
        slots.push({ get: () => a.name, set: (v) => { a.name = v; } });
      }
    }
  }
  return slots;
}

/**
 * Verify that none of the real values still appear in `text`.
 * Returns the number of originals still present (never their values).
 */
export function countResidualOriginals(text, pairs) {
  let n = 0;
  for (const [orig, repl] of pairs || []) {
    if (!orig || !orig.trim()) continue;
    if (repl && repl.includes(orig)) continue; // fake legitimately embeds the real
    if (text.includes(orig)) n++;
  }
  return n;
}

// ── filenames ─────────────────────────────────────────────

const EXT_BY_MIME = {
  "application/pdf": ".pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
  "application/vnd.ms-excel": ".xls",
  "application/msword": ".doc",
  "text/plain": ".txt",
  "text/csv": ".csv",
  "text/markdown": ".md",
  "application/json": ".json",
};

export function sniffExtension(bytes, contentType) {
  if (bytes.length >= 4 && bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46) {
    return ".pdf"; // %PDF
  }
  const ct = (contentType || "").split(";")[0].trim().toLowerCase();
  return EXT_BY_MIME[ct] || "";
}

export function splitFilename(name) {
  const i = name.lastIndexOf(".");
  if (i <= 0) return { stem: name, ext: "" };
  return { stem: name.slice(0, i), ext: name.slice(i) };
}

// ── Gemini (gemini.google.com) ────────────────────────────
//
// Prompt = POST .../BardFrontendService/StreamGenerate, urlencoded form:
//   f.req = JSON.stringify([null, JSON.stringify(inner)])   at = <token>   ...
//   inner[0] = [promptText, 0, null, files, ...]   files = [[[url], filename], ...]
//   inner[2] = [conversationId, responseId, choiceId] (when continuing a chat)
// (format taken from reverse-engineered clients; unknown shapes fail closed)

export function parseGeminiBody(bytes) {
  const params = new URLSearchParams(new TextDecoder().decode(bytes));
  const freq = params.get("f.req");
  if (!freq) throw new Error("no f.req");
  const outer = JSON.parse(freq);
  if (!Array.isArray(outer) || typeof outer[1] !== "string") throw new Error("unexpected f.req shape");
  const inner = JSON.parse(outer[1]);
  if (!Array.isArray(inner) || !Array.isArray(inner[0])) throw new Error("unexpected inner shape");
  return { params, outer, inner };
}

export function geminiConversationId(state) {
  const c = state.inner[2];
  return Array.isArray(c) && typeof c[0] === "string" && c[0] ? c[0] : null;
}

export function collectGeminiSlots(state) {
  const msg = state.inner[0];
  const text = [];
  const names = [];
  if (typeof msg[0] === "string") {
    text.push({ get: () => msg[0], set: (v) => { msg[0] = v; } });
  }
  if (Array.isArray(msg[3])) {
    for (const f of msg[3]) {
      if (Array.isArray(f) && typeof f[1] === "string") {
        names.push({ get: () => f[1], set: (v) => { f[1] = v; } });
      }
    }
  }
  return { text, names };
}

export function buildGeminiBody(state) {
  state.outer[1] = JSON.stringify(state.inner);
  state.params.set("f.req", JSON.stringify(state.outer));
  return new TextEncoder().encode(state.params.toString());
}

// ── multipart/form-data (binary safe) ─────────────────────

function indexOfBytes(hay, needle, from = 0) {
  const n = needle.length;
  outer: for (let i = from; i <= hay.length - n; i++) {
    if (hay[i] !== needle[0]) continue;
    for (let j = 1; j < n; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

export function boundaryFromContentType(ct) {
  const m = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(ct || "");
  return m ? (m[1] || m[2]) : null;
}

export function parseMultipart(bytes, boundary) {
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const delim = enc.encode(`--${boundary}`);
  const headerEnd = enc.encode("\r\n\r\n");
  const parts = [];
  let pos = indexOfBytes(bytes, delim, 0);
  while (pos !== -1) {
    const start = pos + delim.length;
    if (bytes[start] === 0x2d && bytes[start + 1] === 0x2d) break; // closing --
    const next = indexOfBytes(bytes, delim, start);
    if (next === -1) throw new Error("unterminated multipart");
    let chunk = bytes.subarray(start + 2, next - 2); // strip CRLF after delim and before next delim
    const he = indexOfBytes(chunk, headerEnd, 0);
    if (he === -1) throw new Error("bad multipart part");
    const headers = dec.decode(chunk.subarray(0, he));
    const part = { headers: [], bytes: chunk.subarray(he + 4), name: null, filename: null, contentType: null };
    for (const line of headers.split("\r\n")) {
      const c = line.indexOf(":");
      if (c === -1) continue;
      const k = line.slice(0, c).trim(), v = line.slice(c + 1).trim();
      part.headers.push([k, v]);
      if (k.toLowerCase() === "content-disposition") {
        part.name = (/\bname="([^"]*)"/.exec(v) || [])[1] ?? null;
        part.filename = (/filename="([^"]*)"/.exec(v) || [])[1] ?? null;
      } else if (k.toLowerCase() === "content-type") part.contentType = v;
    }
    parts.push(part);
    pos = next;
  }
  return parts;
}

export function buildMultipart(parts, boundary) {
  const enc = new TextEncoder();
  const chunks = [];
  for (const p of parts) {
    chunks.push(enc.encode(`--${boundary}\r\n`));
    const hdrs = p.headers.map(([k, v]) => {
      if (k.toLowerCase() === "content-disposition" && p.filename !== null) {
        v = v.replace(/filename="[^"]*"/, () => `filename="${p.filename.replace(/"/g, "%22")}"`);
      }
      return `${k}: ${v}\r\n`;
    }).join("");
    chunks.push(enc.encode(hdrs + "\r\n"), p.bytes, enc.encode("\r\n"));
  }
  chunks.push(enc.encode(`--${boundary}--\r\n`));
  return concatBytes(chunks);
}
