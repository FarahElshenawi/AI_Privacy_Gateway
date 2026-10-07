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

function pushContentSlots(content, slots) {
  if (!content) return;
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
    pushContentSlots(msg && msg.content, slots);
  }
  return slots;
}

/**
 * /backend-api/f/conversation/prepare carries the prompt text in
 * `partial_query` (the same shape as a message). It must be masked with the
 * same vault as the real send so the surrogate values match.
 */
export function collectPrepareSlots(parsed) {
  const slots = [];
  const pq = parsed && parsed.partial_query;
  if (!pq || typeof pq !== "object") return slots;
  pushContentSlots(pq.content, slots);
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

// ── conversation id from the tab URL ──────────────────────

/**
 * ChatGPT: /c/<uuid> (also /g/<gpt>/c/<uuid>, /g/g-p-…/c/<uuid>) → "<uuid>"
 * Gemini:  /app/<id> (also /u/N/app/<id>) → "c_<id>" (matches the id in f.req)
 * Returns null for a brand-new chat.
 */
export function convIdFromUrl(url) {
  try {
    const u = new URL(url);
    if (u.hostname === "gemini.google.com") {
      const m = /\/app\/([0-9a-zA-Z_-]+)/.exec(u.pathname);
      return m ? `c_${m[1]}` : null;
    }
    const m = /\/c\/([0-9a-zA-Z-]{8,})/.exec(u.pathname);
    return m ? m[1] : null;
  } catch {
    return null;
  }
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

function bytesContainAscii(bytes, needle) {
  const n = needle.length;
  outer: for (let i = 0; i <= bytes.length - n; i++) {
    for (let j = 0; j < n; j++) if (bytes[i + j] !== needle.charCodeAt(j)) continue outer;
    return true;
  }
  return false;
}

/**
 * Format from magic bytes only: ".pdf", ".docx", ".xlsx" or "".
 * OOXML files are ZIPs; entry names are stored in plain ASCII in the local
 * headers (start of file) and the central directory (end of file), so scanning
 * a window at both ends identifies them without unzipping.
 */
export function sniffBinaryExtension(bytes) {
  if (bytes.length >= 4 && bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46) {
    return ".pdf"; // %PDF
  }
  if (bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04) {
    const W = 256 * 1024;
    const windows = bytes.length <= 2 * W
      ? [bytes]
      : [bytes.subarray(0, W), bytes.subarray(bytes.length - W)];
    for (const w of windows) {
      if (bytesContainAscii(w, "word/document.xml")) return ".docx";
      if (bytesContainAscii(w, "xl/workbook.xml")) return ".xlsx";
    }
  }
  return "";
}

export function sniffExtension(bytes, contentType) {
  const bin = sniffBinaryExtension(bytes);
  if (bin) return bin;
  const ct = (contentType || "").split(";")[0].trim().toLowerCase();
  return EXT_BY_MIME[ct] || "";
}

/**
 * The backend picks the handler from the file's bytes AND extension (a ZIP is a
 * Word/Excel file only if it is named .docx/.xlsx). Browsers and upload flows
 * don't always preserve a usable name, so make the extension agree with the
 * content. Never changes the stem (it may already be masked).
 */
export function fixFilename(name, bytes, contentType) {
  const sniffed = sniffBinaryExtension(bytes);
  const { stem, ext } = splitFilename(name || "");
  if (sniffed) return (ext.toLowerCase() === sniffed ? name : (stem || "uploaded_file") + sniffed);
  if (name && ext) return name;
  return (stem || "uploaded_file") + sniffExtension(bytes, contentType);
}

// ── backend error → short, content-free, user-facing reason ──

const FILE_REASONS = {
  image_file: "Images can't be masked (no OCR) — blocked",
  image_files_need_ocr_not_supported: "Images can't be masked (no OCR) — blocked",
  unknown_file_type: "Unsupported file type — only text, PDF, Word (.docx) and Excel (.xlsx) can be masked",
  no_extractable_text: "No extractable text (scanned file?) — can't verify it is clean",
  residual_leak: "Masked file still contained sensitive data",
  detection_blocked_critical_detector_failed: "Detection engine failed — blocked",
  degraded_coverage_strict_mode: "Detection coverage incomplete (strict mode)",
};

function codeList(v) {
  return Array.isArray(v) ? v.filter((x) => typeof x === "string" && /^[A-Za-z0-9_:.-]{1,60}$/.test(x)) : [];
}

/**
 * Build a reason from the backend's HTTP status and (optional) JSON `detail`.
 * Only machine codes from the backend are echoed — never free text, so a
 * misbehaving backend can't push document content into the activity log.
 */
export function describeBackendError(status, detail, kind = "text") {
  if (status === 401 || status === 403) return "Backend rejected the token — retry or reset token in the popup";
  if (status === 413) {
    return kind === "file" ? "File too large for the local backend (50 MB limit)"
                           : "Prompt too long for the local backend (200,000 character limit)";
  }
  if (status === 503) return "Detection or masking engine failed in the backend";
  if (detail && typeof detail === "object") {
    const err = typeof detail.error === "string" ? detail.error : "";
    const blockers = codeList(detail.blockers);
    const leaks = codeList(detail.leak_types);
    for (const c of [err, ...blockers]) if (FILE_REASONS[c]) return FILE_REASONS[c];
    const parts = [];
    if (/^[A-Za-z0-9_:.-]{1,80}$/.test(err)) parts.push(err);
    if (blockers.length) parts.push(`blockers: ${blockers.join(", ")}`);
    if (leaks.length) parts.push(`leaks: ${leaks.join(", ")}`);
    if (parts.length) return `Backend refused the ${kind === "file" ? "file" : "text"} (${parts.join("; ")})`;
  }
  return `Backend error ${status}`;
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
//
// File upload = Google resumable protocol on push.clients6.google.com:
//   1. POST  x-goog-upload-command: start             body: "File name=<name>" (urlencoded)
//   2. POST  x-goog-upload-command: upload, finalize  body: raw file bytes

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

/** Returns the file name in a resumable-upload "start" body, or null if absent. */
export function parseGeminiStartName(bytes) {
  const params = new URLSearchParams(new TextDecoder().decode(bytes));
  const n = params.get("File name");
  return typeof n === "string" && n ? n : null;
}

export function buildGeminiStartBody(name) {
  return new TextEncoder().encode(new URLSearchParams({ "File name": name }).toString());
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
