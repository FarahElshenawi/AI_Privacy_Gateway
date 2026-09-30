// Popup logic — status, toggle, stats, token reset.

const statusDot = document.getElementById("statusDot");
const backendStatus = document.getElementById("backendStatus");
const toggle = document.getElementById("toggle");
const resetBtn = document.getElementById("resetToken");

// Stats elements
const promptsMasked = document.getElementById("promptsMasked");
const entitiesMasked = document.getElementById("entitiesMasked");
const filesMasked = document.getElementById("filesMasked");
const blocks = document.getElementById("blocks");

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

// --- Toggle protection on/off ---

chrome.storage.local.get("protectionEnabled", (result) => {
  const enabled = result.protectionEnabled !== false; // default true
  toggle.classList.toggle("active", enabled);
});

toggle.addEventListener("click", () => {
  const isActive = toggle.classList.toggle("active");
  chrome.storage.local.set({ protectionEnabled: isActive });
});

// --- Load stats ---

chrome.storage.local.get(
  ["promptsMasked", "entitiesMasked", "filesMasked", "blocksCount"],
  (result) => {
    promptsMasked.textContent = result.promptsMasked || 0;
    entitiesMasked.textContent = result.entitiesMasked || 0;
    filesMasked.textContent = result.filesMasked || 0;
    blocks.textContent = result.blocksCount || 0;
  }
);

// --- Reset token ---

resetBtn.addEventListener("click", async () => {
  if (confirm("Reset the backend token? You'll need to restart the backend.")) {
    await chrome.runtime.sendMessage({ type: "RESET_TOKEN" });
    checkHealth();
  }
});

// --- Init ---

checkHealth();
