// Popup logic — status, escape-hatch toggle (logged), stats, audit log, token reset.

const statusDot = document.getElementById("statusDot");
const backendStatus = document.getElementById("backendStatus");
const toggle = document.getElementById("toggle");
const escapeWarning = document.getElementById("escapeWarning");
const resetBtn = document.getElementById("resetToken");
const refreshLogBtn = document.getElementById("refreshLog");
const auditLogEl = document.getElementById("auditLog");

// Stats elements
const promptsMasked = document.getElementById("promptsMasked");
const entitiesMasked = document.getElementById("entitiesMasked");
const filesMasked = document.getElementById("filesMasked");
const blocks = document.getElementById("blocks");
const bypassCount = document.getElementById("bypassCount");

// --- Check backend health ---

async function checkHealth() {
  statusDot.className = "status-dot connecting";
  backendStatus.textContent = "Checking...";

  try {
    const resp = await chrome.runtime.sendMessage({ type: "HEALTH" });
    if (resp && resp.success) {
      statusDot.className = "status-dot online";
      backendStatus.textContent = "Online";
    } else {
      statusDot.className = "status-dot offline";
      backendStatus.textContent = "Offline";
    }
  } catch {
    statusDot.className = "status-dot offline";
    backendStatus.textContent = "Offline";
  }
}

// --- Escape hatch toggle ---
//
// "Protection Active" OFF is the escape hatch: fetch-override.js and
// file-upload-override.js check this same storage key before every
// request and send unmasked if it's off. Every toggle flip, and every
// request that goes out because of it, is written to the audit log
// (service-worker.js logAuditEvent) — this is what makes it a *logged*
// escape hatch rather than a silent kill switch.

function renderProtectionState(enabled) {
  toggle.classList.toggle("active", enabled);
  escapeWarning.classList.toggle("visible", !enabled);
}

chrome.storage.local.get("protectionEnabled", (result) => {
  const enabled = result.protectionEnabled !== false; // default true
  renderProtectionState(enabled);
});

toggle.addEventListener("click", async () => {
  const wasActive = toggle.classList.contains("active");
  const nowEnabled = !wasActive;
  renderProtectionState(nowEnabled);
  await chrome.storage.local.set({ protectionEnabled: nowEnabled });

  // Log from the popup side too (independent of whatever page the MAIN
  // world happens to be running in right now) so the flip itself is
  // always recorded even if no request immediately follows it.
  await chrome.runtime.sendMessage({
    type: "LOG_EVENT",
    payload: {
      event: nowEnabled ? "PROTECTION_ENABLED" : "PROTECTION_DISABLED",
      details: { source: "popup" },
    },
  });

  loadAuditLog();
});

// --- Load stats ---

function loadStats() {
  chrome.storage.local.get(
    ["promptsMasked", "entitiesMasked", "filesMasked", "blocksCount", "bypassCount"],
    (result) => {
      promptsMasked.textContent = result.promptsMasked || 0;
      entitiesMasked.textContent = result.entitiesMasked || 0;
      filesMasked.textContent = result.filesMasked || 0;
      blocks.textContent = result.blocksCount || 0;
      bypassCount.textContent = result.bypassCount || 0;
    }
  );
}

// --- Audit log ---

const BLOCK_EVENTS = new Set([
  "BLOCKED_MASK_FAILURE",
  "BLOCKED_RESIDUAL_LEAK",
  "BLOCKED_EXCEPTION",
  "BLOCKED_FILE_MASK_FAILURE",
  "BLOCKED_FILE_EXCEPTION",
]);
const BYPASS_EVENTS = new Set([
  "ESCAPE_HATCH_BYPASS",
  "ESCAPE_HATCH_BYPASS_FILE",
  "PROTECTION_DISABLED",
]);

function formatAuditEntry(entry) {
  const time = new Date(entry.ts).toLocaleTimeString();
  const cls = BLOCK_EVENTS.has(entry.event)
    ? "block"
    : BYPASS_EVENTS.has(entry.event)
    ? "bypass"
    : "";
  const div = document.createElement("div");
  div.className = "audit-entry";
  div.innerHTML = `<span class="audit-event ${cls}">${entry.event}</span> <span class="audit-time">— ${time}</span>`;
  return div;
}

async function loadAuditLog() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "GET_AUDIT_LOG" });
    const log = (resp && resp.log) || [];
    auditLogEl.innerHTML = "";
    if (log.length === 0) {
      auditLogEl.innerHTML = '<div class="audit-empty">No events yet.</div>';
      return;
    }
    // newest first, cap what we render (the storage log itself is capped at 500)
    for (const entry of log.slice(-30).reverse()) {
      auditLogEl.appendChild(formatAuditEntry(entry));
    }
  } catch {
    auditLogEl.innerHTML = '<div class="audit-empty">Could not load audit log.</div>';
  }
  loadStats();
}

refreshLogBtn.addEventListener("click", loadAuditLog);

// --- Reset token ---

resetBtn.addEventListener("click", async () => {
  if (confirm("Reset the backend token? You'll need to restart the backend.")) {
    await chrome.runtime.sendMessage({ type: "RESET_TOKEN" });
    await chrome.runtime.sendMessage({
      type: "LOG_EVENT",
      payload: { event: "TOKEN_RESET", details: { source: "popup" } },
    });
    checkHealth();
    loadAuditLog();
  }
});

// --- Init ---

checkHealth();
loadAuditLog();
