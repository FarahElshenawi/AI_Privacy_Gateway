/**
 * Doppel — browser-side file interception.
 *
 * Replaces files the user attaches with their MASKED version BEFORE the page's own code can
 * read them. It never detects or masks anything itself: the bytes go to the extension service
 * worker, which asks the local backend and returns the masked file.
 *
 * Covered attach paths on the page: the file picker (<input type=file> "change"), drag-and-drop
 * ("drop") and paste ("paste" with files). For each, the original event is stopped in the
 * capture phase, and a synthetic event carrying the masked files is dispatched afterwards.
 *
 * Fail closed: if masking fails for ANY file in a batch, NO file is attached and a notice is
 * shown. (If the page ever uploaded anyway, the service worker's network check still masks or
 * blocks it.)
 */

const MAX_MESSAGE_BYTES = 40 * 1024 * 1024; // base64 + message overhead stays under Chrome's limit
const SYNTHETIC = new WeakSet();            // events we dispatched ourselves (don't intercept twice)

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function base64ToBytes(b64) {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

/** Ask the service worker to mask one file. Resolves to a File (masked, or the original if
 *  protection is switched off). Rejects with a readable Error on any failure. */
async function maskOneFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.byteLength > MAX_MESSAGE_BYTES) {
    throw new Error("File is too large to check in the browser (limit 40 MB)");
  }
  let response;
  try {
    response = await chrome.runtime.sendMessage({
      type: "MASK_SELECTED_FILE",
      filename: file.name,
      contentType: file.type || "application/octet-stream",
      bytesBase64: bytesToBase64(bytes),
    });
  } catch (e) {
    throw new Error("Doppel extension is not reachable: " + e.message);
  }
  if (!response || !response.success) {
    throw new Error((response && response.error) || "File masking failed");
  }
  if (response.passthrough) return file;
  return new File([base64ToBytes(response.bytesBase64)], response.filename || file.name, {
    type: response.contentType || file.type || "application/octet-stream",
    lastModified: file.lastModified,
  });
}

/** Mask every file; all-or-nothing. */
async function maskAll(files) {
  const out = [];
  for (const f of files) out.push(await maskOneFile(f));
  return out;
}

function toDataTransfer(files) {
  const dt = new DataTransfer();
  for (const f of files) dt.items.add(f);
  return dt;
}

// ── user-visible notice ────────────────────────────────────────────────
function notify(message) {
  try {
    const el = document.createElement("div");
    el.setAttribute("role", "alert");
    el.textContent = "Doppel blocked this attachment: " + message;
    el.style.cssText =
      "position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:360px;padding:12px 14px;" +
      "background:#7f1d1d;color:#fff;font:13px/1.4 system-ui,sans-serif;border-radius:8px;" +
      "box-shadow:0 4px 16px rgba(0,0,0,.3)";
    (document.body || document.documentElement).appendChild(el);
    setTimeout(() => el.remove(), 8000);
  } catch { /* never let a notice break the page */ }
}

const isFileInput = (t) => typeof HTMLInputElement !== "undefined" && t instanceof HTMLInputElement && t.type === "file";
const busy = new WeakSet(); // inputs currently being processed

// ── 1. file picker ─────────────────────────────────────────────────────
async function onInputChange(event) {
  const input = event.target;
  if (!isFileInput(input) || SYNTHETIC.has(event)) return;
  if (!input.files || input.files.length === 0) return;
  if (busy.has(input)) { event.stopImmediatePropagation(); return; }

  event.stopImmediatePropagation();
  event.preventDefault();
  busy.add(input);
  const originals = Array.from(input.files);
  try {
    const masked = await maskAll(originals);
    input.files = toDataTransfer(masked).files;
    const again = new Event("change", { bubbles: true });
    SYNTHETIC.add(again);
    input.dispatchEvent(again);
  } catch (err) {
    input.value = "";   // nothing is attached
    notify(err.message);
  } finally {
    busy.delete(input);
  }
}

// ── 2. drag and drop ───────────────────────────────────────────────────
async function onDrop(event) {
  if (SYNTHETIC.has(event)) return;
  const files = event.dataTransfer && Array.from(event.dataTransfer.files || []);
  if (!files || files.length === 0) return;   // plain text/link drops are the text path's job

  event.stopImmediatePropagation();
  event.preventDefault();
  const target = event.target;
  try {
    const masked = await maskAll(files);
    const again = new DragEvent("drop", {
      bubbles: true, cancelable: true, composed: true,
      dataTransfer: toDataTransfer(masked),
      clientX: event.clientX, clientY: event.clientY,
    });
    SYNTHETIC.add(again);
    target.dispatchEvent(again);
  } catch (err) {
    notify(err.message);
  }
}

// ── 3. paste ───────────────────────────────────────────────────────────
async function onPaste(event) {
  if (SYNTHETIC.has(event)) return;
  const files = event.clipboardData && Array.from(event.clipboardData.files || []);
  if (!files || files.length === 0) return;   // plain-text paste: not a file

  event.stopImmediatePropagation();
  event.preventDefault();
  const target = event.target;
  try {
    const masked = await maskAll(files);
    const again = new ClipboardEvent("paste", {
      bubbles: true, cancelable: true, composed: true, clipboardData: toDataTransfer(masked),
    });
    SYNTHETIC.add(again);
    target.dispatchEvent(again);
  } catch (err) {
    notify(err.message);
  }
}

// Capture phase: runs before the page's (React) listeners.
function install(doc) {
  doc.addEventListener("change", onInputChange, true);
  doc.addEventListener("drop", onDrop, true);
  doc.addEventListener("paste", onPaste, true);
}

if (typeof document !== "undefined") install(document);

// Test hook (Node): the content script is a classic script in the browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { maskOneFile, maskAll, onInputChange, onDrop, onPaste, install, SYNTHETIC };
}
