// Popup logic — v2.0 with debugger status

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const toggle = document.getElementById("toggle");
const resetBtn = document.getElementById("resetToken");
const activityFeed = document.getElementById("activityFeed");
const debuggerStatus = document.getElementById("debuggerStatus");
const attachBtn = document.getElementById("attachBtn");

// Stats elements
const promptsMasked = document.getElementById("promptsMasked");
const entitiesMasked = document.getElementById("entitiesMasked");
const filesMasked = document.getElementById("filesMasked");
const blocks = document.getElementById("blocks");

// Entity badge colors
const BADGE_COLORS = {
  EMAIL: "email", EMAIL_ADDRESS: "email",
  CREDIT_CARD: "card", CARD_NUMBER: "card",
  PERSON: "name", person: "name",
  PHONE_E164: "phone", PHONE_NUMBER: "phone", phone_number: "phone",
  API_KEY: "key", JWT: "key", PEM_BLOCK: "key", IBAN: "key",
  ORGANIZATION: "name", organization: "name",
  ADDRESS: "name", address: "name",
};

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// --- Check backend health ---
async function checkHealth() {
  statusDot.className = "status-dot connecting";
  statusText.textContent = "Checking";

  try {
    const resp = await chrome.runtime.sendMessage({ type: "HEALTH" });
    if (resp && resp.success) {
      statusDot.className = "status-dot";
      statusText.textContent = "Online";
    } else {
      statusDot.className = "status-dot offline";
      statusText.textContent = "Offline";
    }
  } catch {
    statusDot.className = "status-dot offline";
    statusText.textContent = "Offline";
  }
}

// --- Toggle protection ---
chrome.storage.local.get("protectionEnabled", (result) => {
  const enabled = result.protectionEnabled !== false;
  toggle.classList.toggle("active", enabled);
});

toggle.addEventListener("click", () => {
  const isActive = toggle.classList.toggle("active");
  chrome.storage.local.set({ protectionEnabled: isActive });
});

// --- Toggle: show real values in replies ---
const demaskToggle = document.getElementById("demaskToggle");
chrome.storage.local.get("demaskEnabled", (result) => {
  demaskToggle.classList.toggle("active", result.demaskEnabled !== false);
});
demaskToggle.addEventListener("click", () => {
  const isActive = demaskToggle.classList.toggle("active");
  chrome.storage.local.set({ demaskEnabled: isActive });
});

// --- Load stats ---
function loadStats() {
  chrome.storage.local.get(
    ["promptsMasked", "entitiesMasked", "filesMasked", "blocksCount", "activityLog"],
    (result) => {
      promptsMasked.textContent = result.promptsMasked || 0;
      entitiesMasked.textContent = result.entitiesMasked || 0;
      filesMasked.textContent = result.filesMasked || 0;
      blocks.textContent = result.blocksCount || 0;

      // Render activity feed
      const log = result.activityLog || [];
      if (log.length === 0) {
        activityFeed.innerHTML = '<div class="activity-empty">No activity yet — type a prompt in ChatGPT to see masking in action.</div>';
      } else {
        activityFeed.innerHTML = log.slice(0, 5).map(renderActivity).join("");
      }
    }
  );
}

function renderActivity(item) {
  const iconClass = item.type === "mask" ? "masked" : item.type === "block" ? "blocked" : "file";
  const icon = item.type === "mask" ? "🎭" : item.type === "block" ? "🚫" : "📄";
  const title = item.type === "mask" ? "Prompt Masked" : item.type === "block" ? "Request Blocked" : "File Masked";

  const badges = (item.entityTypes || []).map(type => {
    const colorClass = BADGE_COLORS[type] || "name";
    return `<span class="entity-badge ${colorClass}">${esc(type)}</span>`;
  }).join("");

  const detail = item.entityCount
    ? `${esc(item.entityCount)} entities detected & masked`
    : esc(item.detail || "");

  return `
    <div class="activity-item">
      <div class="activity-icon ${iconClass}">${icon}</div>
      <div class="activity-content">
        <div class="activity-title">${title}</div>
        <div class="activity-detail">${detail}</div>
        ${badges ? `<div class="activity-badges">${badges}</div>` : ""}
      </div>
      <div class="activity-time">${esc(item.time || "")}</div>
    </div>
  `;
}

// --- Reset token ---
resetBtn.addEventListener("click", async () => {
  if (confirm("Reset the backend token? The backend must be restarted.")) {
    await chrome.runtime.sendMessage({ type: "RESET_TOKEN" });
    checkHealth();
  }
});

// --- Listen for real-time updates from the service worker ---
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ACTIVITY_UPDATE") {
    loadStats();
  }
});

// --- Init ---
checkHealth();
loadStats();
checkDebuggerStatus();

// Auto-refresh every 5 seconds
setInterval(() => {
  loadStats();
  checkDebuggerStatus();
}, 5000);

// --- Debugger status ---
async function checkDebuggerStatus() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "GET_DEBUGGER_STATUS" });
    if (resp && resp.success) {
      if (resp.count === 0) {
        debuggerStatus.innerHTML = '<span style="color: var(--red);">⚠ Not attached to any tab</span>';
        attachBtn.textContent = "Attach to current tab";
        attachBtn.style.display = "block";
      } else {
        const tabs = resp.attachedTabs || [];
        const tabList = tabs.map(t => {
          // Try to extract just the host
          let host = "unknown";
          try { host = new URL(t.url).hostname; } catch {}
          return `tab ${esc(t.tabId)} (${esc(host)})`;
        }).join(", ");
        debuggerStatus.innerHTML = `<span style="color: var(--green);">● Attached to ${esc(resp.count)} tab(s):</span><br><span style="font-size: 10px; color: var(--muted);">${tabList}</span>`;
        attachBtn.textContent = "Detach from current tab";
        attachBtn.style.display = "block";
      }
    } else {
      debuggerStatus.innerHTML = '<span style="color: var(--amber);">Status unknown</span>';
    }
  } catch (e) {
    debuggerStatus.innerHTML = `<span style="color: var(--red);">SW not responding</span>`;
  }
}

attachBtn.addEventListener("click", async () => {
  attachBtn.disabled = true;
  attachBtn.textContent = "Working...";
  try {
    // Determine if we should attach or detach based on current state
    const status = await chrome.runtime.sendMessage({ type: "GET_DEBUGGER_STATUS" });
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const isAttached = status?.attachedTabs?.some(t => t.tabId === tab?.id);

    if (isAttached) {
      await chrome.runtime.sendMessage({ type: "DETACH_NOW" });
    } else {
      const resp = await chrome.runtime.sendMessage({ type: "ATTACH_NOW" });
      if (!resp.success) {
        alert("Could not attach: " + resp.error);
      }
    }
  } catch (e) {
    alert("Error: " + e.message);
  }
  attachBtn.disabled = false;
  checkDebuggerStatus();
});

// --- Diagnostics (content-free) ---
async function loadDiag() {
  const el = document.getElementById("diag");
  if (!el) return;
  try {
    const r = await chrome.runtime.sendMessage({ type: "GET_DIAG" });
    const lines = [
      `attached tabs: ${(r.attached || []).join(", ") || "none"}`,
      `last attach: ${r.diag.lastAttach || "never"}`,
      `last detach: ${r.diag.lastDetach || "never"}`,
      "--- recent requests ---",
      ...(r.trace || []).map((e) => `${e.t} ${e.method || ""} ${e.host || ""} ${e.kind || ""} ${e.outcome || ""} ${e.detail || ""}`.replace(/\s+/g, " ")),
    ];
    el.textContent = lines.join("\n");
  } catch (e) {
    el.textContent = "Service worker not responding: " + e.message;
  }
}
loadDiag();
setInterval(loadDiag, 3000);
