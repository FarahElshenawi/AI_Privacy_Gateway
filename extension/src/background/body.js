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
