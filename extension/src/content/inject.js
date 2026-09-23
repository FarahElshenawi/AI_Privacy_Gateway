// Injects fetch-override.js into the MAIN world (isolated-world content scripts
// cannot see page fetches). See docs/architecture.md 1.4.
const script = document.createElement("script");
script.src = chrome.runtime.getURL("src/content/fetch-override.js");
script.onload = function () { this.remove(); };
(document.head || document.documentElement).appendChild(script);
